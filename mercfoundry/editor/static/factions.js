// Factions: new names for the four employers (factions.json), with a preview
// of the game's text they change.

export function createFactions(ctx) {
  const {$, status, setDirty} = ctx;
  const st = {doc: {}, old: {}, list: [], people: {}, models: [], vehicles: {}, dirty: false, loaded: false};
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const LABELS = {allies: 'Allies', china: 'China', mafia: 'Russian Mafia', sk: 'South Korea'};
  const FIELDS = [['name', 'Name', 'e.g. Yakuza'], ['adjective', 'Adjective', 'as in "<x> HQ" (default: the name)'],
                  ['short', 'Short form', 'as in "Mafia", "SK" (default: the name)'],
                  ['people', 'People', 'as in "witnessed by <x>" (default: the adjective)']];

  async function load() {
    const r = await fetch('/api/factions');
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
    st.doc = d.doc || {};
    st.old = d.old || {};
    st.list = d.factions || [];
    st.people = d.people || {};
    st.vehicles = d.vehicles || {};
    st.models = d.models || [];
    st.loaded = true;
    undoBase = JSON.stringify(st.doc);
    draw();
  }

  function draw() {
    $('fx-rows').innerHTML = st.list.map((f) => {
      const v = st.doc[f] || {};
      const looks = v.looks || {};
      const people = (st.people[f] || []).map((p) => `<label class="sky-row" title="${p.templates} template(s)${p.shared.length ? '; also drawn by ' + p.shared.join(', ') + "'s, which change too" : ''}">
          <span>${esc(p.model)}${p.shared.length ? ' *' : ''}</span>
          <input type="text" list="fx-models" data-look="${f}" data-model="${esc(p.model)}" value="${esc(looks[p.model] || '')}" placeholder="keep it"></label>`).join('');
      const vehicles = (st.vehicles[f] || []).map((p) => `<label class="sky-row" title="${esc(p.kind)}, ${p.templates} template(s)${p.shared.length ? '; also drawn by ' + p.shared.join(', ') + "'s, which change too" : ''}">
          <span>${esc(p.model)}${p.shared.length ? ' *' : ''}</span>
          <select data-look="${f}" data-model="${esc(p.model)}"><option value="">keep it</option>
            ${p.can.map((m) => `<option${looks[p.model] === m ? ' selected' : ''}>${esc(m)}</option>`).join('')}
            ${looks[p.model] && !p.can.includes(looks[p.model]) ? `<option selected>${esc(looks[p.model])}</option>` : ''}</select></label>`).join('');
      return `<div class="ms-obj"><b>${esc(LABELS[f] || f)}</b> <span class="hint">(${esc((st.old[f] || []).join(', '))})</span>
        ${FIELDS.map(([k, label, ph]) => `<label class="sky-row"><span>${label}</span>
          <input type="text" data-f="${f}" data-k="${k}" value="${esc(v[k] || '')}" placeholder="${esc(k === 'name' ? 'keep the game\'s' : ph)}"></label>`).join('')}
        <details${Object.keys(looks).length ? ' open' : ''}><summary>Look: its people's models</summary>
          <p class="hint">Each model its characters draw, and another to draw instead (* a model another faction's characters draw too: theirs change as well).</p>
          ${people}
          ${vehicles ? `<p class="hint">Vehicles: each can be drawn as one of the same kind that has all its seats, doors,
            wheels, guns and rotors (its ruin and wheels go with it).</p>${vehicles}` : ''}
          <div class="buttons">Look like <select data-like="${f}"><option value="">...</option>${st.list.filter((g) => g !== f).map((g) => `<option value="${g}">${esc(LABELS[g] || g)}</option>`).join('')}</select>
            <button data-clear-looks="${f}">Keep its own</button></div>
        </details>
      </div>`;
    }).join('') + `<datalist id="fx-models">${st.models.map((m) => `<option value="${esc(m)}">`).join('')}</datalist>`;
    $('fx-everywhere').checked = !!st.doc.everywhere;
    $('fx-subtitles').checked = !!st.doc.subtitles;
  }

  function edited() {
    st.dirty = true;
    setDirty(true);
    $('fx-out').innerHTML = '';
    clearTimeout(undoTimer);
    undoTimer = setTimeout(flushUndo, 700);       // a burst of typing is one undo step
  }
  // factions.json's edits before and after a change: one step on the editor's history.
  let undoBase = null, undoTimer = null;
  function flushUndo() {
    clearTimeout(undoTimer);
    undoTimer = null;
    if (undoBase === null) return;
    const now = JSON.stringify(st.doc);
    if (now === undoBase) return;
    const before = undoBase, after = now;
    undoBase = now;
    const put = (json) => {
      st.doc = JSON.parse(json);
      undoBase = json;
      st.dirty = true;
      setDirty(true);
      draw();
    };
    ctx.remember && ctx.remember('Edited the factions', () => put(before), () => put(after));
  }

  // a faction's looks: {its model: another}
  function setLook(f, model, other) {
    st.doc[f] = st.doc[f] || {};
    const looks = st.doc[f].looks = st.doc[f].looks || {};
    if (other && other.toLowerCase() !== model.toLowerCase()) looks[model] = other; else delete looks[model];
    if (!Object.keys(looks).length) delete st.doc[f].looks;
    if (!Object.keys(st.doc[f]).length) delete st.doc[f];
  }
  $('fx-rows').addEventListener('change', (e) => {
    const t = e.target;
    if (t.dataset.look) { setLook(t.dataset.look, t.dataset.model, t.value.trim()); edited(); }
    if (t.dataset.like) {
      // each model to the other faction's most used model of the same role
      // (soldier, heavy, elite, officer...); one with no match keeps its own
      const f = t.dataset.like, g = t.value;
      if (!g) return;
      const theirs = st.people[g] || [];
      const of = (role) => (theirs.find((p) => p.role === role) || {}).model;
      for (const p of st.people[f] || []) setLook(f, p.model, of(p.role));
      for (const v of st.vehicles[f] || []) {
        const fit = (st.vehicles[g] || []).find((w) => w.kind === v.kind && v.can.includes(w.model));
        setLook(f, v.model, fit ? fit.model : '');
      }
      edited();
      draw();
      status(`${LABELS[f] || f}'s people look like ${LABELS[g] || g}'s (model by model; change any), Save to keep it`);
    }
  });
  $('fx-rows').addEventListener('click', (e) => {
    const f = e.target.dataset.clearLooks;
    if (!f) return;
    if (st.doc[f]) { delete st.doc[f].looks; if (!Object.keys(st.doc[f]).length) delete st.doc[f]; }
    edited();
    draw();
  });
  $('fx-rows').addEventListener('input', (e) => {
    const f = e.target.dataset.f, k = e.target.dataset.k;
    if (!f || !k) return;
    const v = e.target.value.trim();
    st.doc[f] = st.doc[f] || {};
    if (v) st.doc[f][k] = v; else delete st.doc[f][k];
    if (!Object.keys(st.doc[f]).length) delete st.doc[f];
    edited();
  });
  $('fx-everywhere').onchange = () => { if ($('fx-everywhere').checked) st.doc.everywhere = true; else delete st.doc.everywhere; edited(); };
  $('fx-subtitles').onchange = () => { if ($('fx-subtitles').checked) st.doc.subtitles = true; else delete st.doc.subtitles; edited(); };

  // only factions with a name count (the others keep the game's)
  const body = () => {
    const doc = {};
    for (const f of st.list) if (st.doc[f] && (st.doc[f].name || st.doc[f].looks)) doc[f] = st.doc[f];
    const named = st.list.some((f) => doc[f] && doc[f].name);
    if (named && st.doc.everywhere) doc.everywhere = true;
    if (named && st.doc.subtitles) doc.subtitles = true;
    if (st.doc.map) doc.map = st.doc.map;          // only on that new map (factions.json "map")
    return doc;
  };

  $('fx-preview').onclick = async () => {
    $('fx-out').innerHTML = '<p class="hint">Looking through the game\'s text...</p>';
    try {
      const r = await fetch('/api/factions/preview', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({doc: body()})});
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.statusText);
      $('fx-out').innerHTML = `<p><b>${d.count}</b> string${d.count === 1 ? '' : 's'} change${d.count > 40 ? ' (40 shown)' : ''}:</p>`
        + d.samples.map(([k, a, b]) => `<div class="fx-sample"><span class="hint">${esc(k)}</span><br>`
          + `<s>${esc(a)}</s><br>${esc(b)}</div>`).join('');
    } catch (e) {
      $('fx-out').innerHTML = `<p class="hint">Couldn't preview: ${esc(e.message)}</p>`;
    }
  };

  function show(on) {
    $('factions').hidden = !on;
    $('factions-mode').classList.toggle('on', on);
    if (on && !st.loaded) load().catch((e) => status(e.message, true));
  }
  $('factions-mode').onclick = () => show($('factions').hidden);
  $('fx-close').onclick = () => show(false);

  function saveBody() { return st.dirty ? {doc: body()} : null; }
  function saved() { st.dirty = false; }
  return {show, saveBody, saved, flushUndo};
}
