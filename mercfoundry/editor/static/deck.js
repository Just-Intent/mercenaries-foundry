// Deck of 52: a new map's own suits (deck/<suit>.json): each card's name,
// bio, bounty, portrait and (number cards) the placed character and its
// layer, and "others" for the suits no deck on the map uses. Saved through
// the build's own check (deck.parse), so what saves, builds.

const RANK_NAMES = {2: 'Two', 3: 'Three', 4: 'Four', 5: 'Five', 6: 'Six', 7: 'Seven', 8: 'Eight', 9: 'Nine',
                    10: 'Ten', J: 'Jack', Q: 'Queen', K: 'King', A: 'Ace'};
const RANK_NUM = {J: 11, Q: 12, K: 13, A: 14};

export function createDeck(ctx) {
  const {$, status, post, findObject} = ctx;
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const st = {data: null, cur: 0, dirty: new Set(), open: null, bust: Date.now()};
  const box = document.createElement('div');
  box.id = 'deck';
  box.hidden = true;
  $('panel').prepend(box);                // first in the panel, above the view's hints

  async function load() {
    const r = await fetch('/api/deck');
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
    st.data = d;
    st.dirty.clear();
    if (st.cur >= d.files.length) st.cur = 0;
    draw();
  }

  const file = () => st.data && st.data.files[st.cur];
  const rankNum = (c) => RANK_NUM[String(c).toUpperCase()] || +c;
  function card(doc, rank) {
    doc.cards = doc.cards || [];
    let c = doc.cards.find((x) => String(x.card).toUpperCase() === rank);
    if (!c) { c = {card: rank}; doc.cards.push(c); }
    return c;
  }
  function edited() {
    st.dirty.add(st.cur);
    const b = $('deck-save');
    if (b) b.disabled = false;
  }

  function cardRow(f, rank) {
    const doc = f.doc;
    const c = (doc.cards || []).find((x) => String(x.card).toUpperCase() === rank) || {card: rank};
    const num = rankNum(rank);
    const face = f.portraits[String(num)];
    const isNumber = num <= 10;
    const open = st.open === `${st.cur}:${rank}`;
    return `<details class="deck-card" data-rank="${rank}" ${open ? 'open' : ''}>
      <summary><span class="deck-face">${face ? `<img src="/api/modfile/${encodeURI(face)}?${st.bust}" alt="">` : '<i>no portrait</i>'}</span>
        <span class="deck-title"><b>${esc(RANK_NAMES[rank])}</b> ${esc(c.name || '')}${c.alias ? ` <span class="hint">"${esc(c.alias)}"</span>` : ''}
        ${isNumber && !c.actor ? '<span class="warn"> no character</span>' : ''}</span></summary>
      <label class="sky-row"><span>Name</span><input data-k="name" value="${esc(c.name || '')}" placeholder="the card's person"></label>
      <label class="sky-row"><span>Alias</span><input data-k="alias" value="${esc(c.alias || '')}"></label>
      <label class="sky-row"><span>Role</span><input data-k="role" value="${esc(c.role || '')}"></label>
      <label class="sky-row"><span>Bounty</span><input type="number" data-k="bounty" value="${c.bounty ?? ''}" placeholder="12,500 (kill; alive pays double)"></label>
      <label class="field">Description (the PDA's bio: "Known as alias. Role. Description." unless Bio is set)
        <textarea rows="3" data-k="description">${esc(c.description || '')}</textarea></label>
      <label class="field">Bio (instead, word for word)<textarea rows="2" data-k="bio">${esc(c.bio || '')}</textarea></label>
      ${isNumber ? `<label class="sky-row"><span>Character</span><input data-k="actor" value="${esc(c.actor || '')}" placeholder="its name in an encounters layer">
          ${c.actor ? `<button data-find="${esc(c.actor)}" title="Look at it">Find</button>` : ''}</label>
        <label class="sky-row"><span>Layer</span><input data-k="layer" value="${esc(c.layer || '')}" placeholder="loaded while it's at large (optional)"></label>`
        : '<p class="hint">Face cards and aces come through contracts: a verify objective with this card.</p>'}
      <div class="buttons"><label class="filebtn">Portrait PNG (128x128)<input type="file" accept="image/png" data-face="${rank}"></label></div>
    </details>`;
  }

  function draw() {
    if (box.hidden) return;
    const d = st.data;
    let h = `<div class="panel-head"><h2>Deck of 52</h2><button id="deck-close">Close</button></div>`;
    if (!d) { box.innerHTML = h + '<p class="hint">Loading...</p>'; bindClose(); return; }
    if (!d.maps.length) {
      box.innerHTML = h + `<p class="hint">A Deck of its own is for a mod's new map: make one with the Map tool first.</p>`;
      bindClose();
      return;
    }
    h += `<p class="hint">One file per suit. On its map, the PDA's Deck screen and the verify screen show these
      cards (names, bios, portraits) instead of the game's, and the number cards are found, verified and paid as retail's.</p>
      <div class="tabs">${d.files.map((f, i) => `<button data-tab="${i}" class="${i === st.cur ? 'on' : ''}">${esc((f.doc && f.doc.suit) || f.file)}${st.dirty.has(i) ? ' *' : ''}</button>`).join('')}
        <button id="deck-new" title="A new suit">+ Suit</button></div>`;
    const f = file();
    if (!f) {
      box.innerHTML = h + '<p class="hint">No deck yet: + Suit makes one.</p>';
      bind();
      return;
    }
    if (f.error) { box.innerHTML = h + `<p class="warn">${esc(f.file)}: ${esc(f.error)}</p>`; bind(); return; }
    const doc = f.doc;
    const others = doc.others || null;
    h += `${f.generated === true ? '<p class="warn">This file is written by the mod\'s own tools (mod.json "generated"): edits here are replaced the next time they run.</p>' : ''}
      ${f.problem ? `<p class="warn">${esc(f.problem)}</p>` : ''}
      <label class="sky-row"><span>Suit</span><select id="deck-suit">${d.suits.map((s) => `<option${doc.suit === s ? ' selected' : ''}>${s}</option>`).join('')}</select></label>
      <label class="sky-row"><span>Map</span><select id="deck-map"><option value="">${d.maps.length === 1 ? `(${esc(d.maps[0])})` : 'choose'}</option>
        ${d.maps.map((m) => `<option${doc.map === m ? ' selected' : ''}>${esc(m)}</option>`).join('')}</select></label>
      ${d.ranks.map((r) => cardRow(f, r)).join('')}
      <details class="deck-card"><summary><b>Other suits</b> <span class="hint">${others ? 'shown as a stand-in' : 'the game\'s'}</span></summary>
        <p class="hint">On this map, every card of a suit no deck here uses can show one stand-in name, bio and portrait.</p>
        <label class="check"><input type="checkbox" id="deck-others-on" ${others ? 'checked' : ''}> Use a stand-in</label>
        ${others ? `<label class="sky-row"><span>Name</span><input id="deck-others-name" value="${esc(others.name || '')}"></label>
          <label class="field">Bio<textarea id="deck-others-bio" rows="2">${esc(others.bio || '')}</textarea></label>
          <label class="sky-row"><span>Portrait</span><input id="deck-others-face" value="${esc(others.face || '')}" placeholder="deck/other.png"></label>` : ''}
      </details>
      <div class="buttons sticky-save"><button id="deck-save" ${st.dirty.has(st.cur) ? '' : 'disabled'}>Save ${esc(f.file)}</button>
        <span id="deck-note" class="hint"></span></div>`;
    box.innerHTML = h;
    bind();
  }

  function bindClose() { $('deck-close').onclick = () => show(false); }

  function bind() {
    bindClose();
    box.querySelectorAll('[data-tab]').forEach((b) => b.onclick = () => { st.cur = +b.dataset.tab; draw(); });
    $('deck-new').onclick = async () => {
      const used = new Set(st.data.files.map((f) => f.doc && f.doc.suit));
      const suit = prompt(`Which suit? (${st.data.suits.filter((s) => !used.has(s)).join(', ')})`, st.data.suits.find((s) => !used.has(s)) || '');
      if (!suit) return;
      if (!st.data.suits.includes(suit)) return status('A suit is clubs, diamonds, hearts or spades', true);
      st.data.files.push({file: `deck/${suit}.json`, doc: {suit, ...(st.data.maps.length === 1 ? {} : {map: st.data.maps[0]}),
        cards: st.data.ranks.map((r) => ({card: r, name: ''}))}, portraits: {}, problem: 'Not saved yet: give every card a name.'});
      st.cur = st.data.files.length - 1;
      edited();
      draw();
    };
    const f = file();
    if (!f || !f.doc) return;
    const doc = f.doc;
    const suitSel = $('deck-suit');
    if (suitSel) suitSel.onchange = () => { doc.suit = suitSel.value; edited(); };
    const mapSel = $('deck-map');
    if (mapSel) mapSel.onchange = () => { if (mapSel.value) doc.map = mapSel.value; else delete doc.map; edited(); };
    box.querySelectorAll('details.deck-card[data-rank]').forEach((det) => det.ontoggle = () => {
      if (det.open) st.open = `${st.cur}:${det.dataset.rank}`;
    });
    box.querySelectorAll('[data-k]').forEach((inp) => inp.onchange = () => {
      const c = card(doc, inp.closest('[data-rank]').dataset.rank);
      const v = inp.type === 'number' ? (inp.value === '' ? null : Math.round(+inp.value)) : inp.value.trim();
      if (v === '' || v === null) delete c[inp.dataset.k]; else c[inp.dataset.k] = v;
      edited();
    });
    box.querySelectorAll('[data-find]').forEach((b) => b.onclick = (e) => {
      e.preventDefault();
      if (!findObject(b.dataset.find)) status(`${b.dataset.find} isn't on this map's loaded layers (its layer may load only in the game)`, true);
    });
    box.querySelectorAll('[data-face]').forEach((inp) => inp.onchange = async () => {
      const fl = inp.files[0];
      if (!fl) return;
      const b64 = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1]); r.readAsDataURL(fl); });
      try {
        const r = await post('/api/deck/portrait', {suit: doc.suit, card: inp.dataset.face, png: b64});
        status(r.warning ? `Portrait saved (${r.file}); ${r.warning}` : `Portrait saved: ${r.file}`, !!r.warning);
        st.bust = Date.now();
        const keep = st.cur;
        await load();
        st.cur = keep;
        draw();
      } catch (e) { status(e.message, true); }
    });
    const on = $('deck-others-on');
    if (on) on.onchange = () => { if (on.checked) doc.others = {name: 'Unknown', bio: ''}; else delete doc.others; edited(); draw(); };
    for (const [id, k] of [['deck-others-name', 'name'], ['deck-others-bio', 'bio'], ['deck-others-face', 'face']]) {
      const inp = $(id);
      if (inp) inp.onchange = () => { if (inp.value.trim()) doc.others[k] = inp.value.trim(); else delete doc.others[k]; edited(); };
    }
    $('deck-save').onclick = async () => {
      try {
        const r = await post('/api/deck/save', {file: f.file, doc});
        status(`Saved ${r.file}`);
        const keep = st.cur;
        await load();
        st.cur = keep;
        draw();
      } catch (e) { $('deck-note').textContent = e.message; status(`Not saved: ${e.message}`, true); }
    };
  }

  function show(on) {
    box.hidden = !on;
    $('deck-mode').classList.toggle('on', on);
    if (on && !st.data) load().catch((e) => status(e.message, true));
    else if (on) draw();
  }
  $('deck-mode').onclick = () => show(box.hidden);
  return {show, load, isDirty: () => st.dirty.size > 0};
}
