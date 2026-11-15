// Factions: new names for the four employers (factions.json), with a preview
// of the game's text they change.

export function createFactions(ctx) {
  const {$, status, setDirty} = ctx;
  const st = {doc: {}, old: {}, list: [], dirty: false, loaded: false};
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
    st.loaded = true;
    draw();
  }

  function draw() {
    $('fx-rows').innerHTML = st.list.map((f) => {
      const v = st.doc[f] || {};
      return `<div class="ms-obj"><b>${esc(LABELS[f] || f)}</b> <span class="hint">(${esc((st.old[f] || []).join(', '))})</span>
        ${FIELDS.map(([k, label, ph]) => `<label class="sky-row"><span>${label}</span>
          <input type="text" data-f="${f}" data-k="${k}" value="${esc(v[k] || '')}" placeholder="${esc(k === 'name' ? 'keep the game\'s' : ph)}"></label>`).join('')}
      </div>`;
    }).join('');
    $('fx-everywhere').checked = !!st.doc.everywhere;
    $('fx-subtitles').checked = !!st.doc.subtitles;
  }

  function edited() { st.dirty = true; setDirty(true); $('fx-out').innerHTML = ''; }

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
    for (const f of st.list) if (st.doc[f] && st.doc[f].name) doc[f] = st.doc[f];
    if (Object.keys(doc).length && st.doc.everywhere) doc.everywhere = true;
    if (Object.keys(doc).length && st.doc.subtitles) doc.subtitles = true;
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
  return {show, saveBody, saved};
}
