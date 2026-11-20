// Sounds & effects: the game's sound banks (hear a cue, replace its sound,
// add a new cue, or copy one under a new name with tracks of your own:
// sounds/<bank>/...), and the effects table (effects.json: an entry's values
// changed, or a copy under a new name, e.g. a helicopter's own engine set).

export function createSounds(ctx) {
  const {$, status, post} = ctx;
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const st = {banks: [], bank: '', cues: [], filter: '', cue: null, fx: null, fxFilter: '', fxEntry: '', fxDoc: {}, fxDirty: false};
  const box = document.createElement('div');
  box.id = 'sound';
  box.hidden = true;
  $('panel').prepend(box);
  const audio = new Audio();

  async function loadBank(b) {
    st.bank = b;
    st.cue = null;
    st.cues = b ? (await (await fetch(`/api/sound/bank/${encodeURIComponent(b)}`)).json()).cues || [] : [];
    draw();
  }
  async function load() {
    st.banks = await (await fetch('/api/sound/banks')).json();
    const fx = await (await fetch('/api/effects')).json();
    st.fx = fx.table;
    st.fxDoc = fx.doc || {};
    st.fxDirty = false;
    draw();
  }
  const b64 = (file) => new Promise((res) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1]); r.readAsDataURL(file); });

  function draw() {
    if (box.hidden) return;
    const q = st.filter.toLowerCase();
    const list = st.cues.filter((c) => !q || c.name.toLowerCase().includes(q)).slice(0, 80);
    const c = st.cue && st.cues.find((x) => x.name === st.cue);
    const fq = st.fxFilter.toLowerCase();
    const fxNames = st.fx ? Object.keys(st.fx).filter((n) => !fq || n.toLowerCase().includes(fq)).slice(0, 60) : [];
    const e = st.fxEntry && (st.fx[st.fxEntry] || (st.fxDoc[st.fxEntry] && st.fxDoc[st.fxEntry].copy && st.fx[st.fxDoc[st.fxEntry].copy]) || null);
    const edits = st.fxDoc[st.fxEntry] || {};
    box.innerHTML = `<div class="panel-head"><h2>Sounds &amp; effects</h2><button id="snd-close">Close</button></div>
      <h3>Sounds</h3>
      <label class="sky-row"><span>Bank</span><select id="snd-bank"><option value="">choose a bank</option>
        ${st.banks.map((b) => `<option ${b === st.bank ? 'selected' : ''}>${esc(b)}</option>`).join('')}</select></label>
      ${st.bank ? `<input id="snd-filter" type="search" value="${esc(st.filter)}" placeholder="find a cue in ${esc(st.bank)}">
        <div class="snd-list">${list.map((x) => `<div class="snd-cue ${x.name === st.cue ? 'on' : ''}" data-cue="${esc(x.name)}">
          <span>${esc(x.name)}</span><span class="hint">${x.waves} sound${x.waves === 1 ? '' : 's'}${x.mod.length ? ' &middot; ' + x.mod.join(', ') : ''}${x.like ? ` (like ${esc(x.like)})` : ''}</span></div>`).join('')}</div>
        ${c ? `<div class="snd-detail"><b>${esc(st.bank)}.${esc(c.name)}</b>
          <div class="buttons">${Array.from({length: Math.max(1, Math.min(c.waves, 8))}, (_, i) => `<button data-play="${i}">&#9654; ${c.waves > 1 ? i + 1 : 'Hear it'}</button>`).join('')}
            <button id="snd-stop">Stop</button></div>
          ${c.mod.includes('new') ? (c.like ? `<p class="hint">A copy of ${esc(c.like)}: give a track your own sound.</p>
              <div class="buttons"><input id="snd-track" type="number" min="1" value="1" style="width:56px"><label class="filebtn">Track's .wav<input type="file" accept=".wav,audio/wav" id="snd-track-file"></label></div>`
            : '<p class="hint">A new cue of the mod\'s own.</p>')
          : `<div class="buttons"><label class="filebtn">Replace with a .wav<input type="file" accept=".wav,audio/wav" id="snd-replace"></label></div>
            <div class="buttons"><input id="snd-copy-name" placeholder="new cue's name"><button id="snd-copy">Copy as a new cue</button></div>
            <p class="hint">A copy keeps every track and setting (a helicopter's rotor-speed layers); give its tracks your sounds, and point an effect set at it below.</p>`}
        </div>` : ''}
        <div class="buttons"><input id="snd-new-name" placeholder="a new cue's name"><label class="filebtn">New cue from a .wav<input type="file" accept=".wav,audio/wav" id="snd-new-file"></label></div>` : ''}
      <h3>Effects</h3>
      <p class="hint">The game's effects table: sound and effect sets (a template's effectset names one), chatter,
        impacts. Change an entry's values, or copy one as a new entry (e.g. a helicopter set with its own engine cue).</p>
      <input id="fx-filter" type="search" value="${esc(st.fxFilter)}" placeholder="find an entry, e.g. littlebird">
      <div class="snd-list">${fxNames.map((n) => `<div class="snd-cue ${n === st.fxEntry ? 'on' : ''}" data-fx="${esc(n)}"><span>${esc(n)}</span>${st.fxDoc[n] ? '<span class="hint">changed</span>' : ''}</div>`).join('')}
        ${Object.keys(st.fxDoc).filter((n) => !st.fx[n] && (!fq || n.toLowerCase().includes(fq))).map((n) => `<div class="snd-cue ${n === st.fxEntry ? 'on' : ''}" data-fx="${esc(n)}"><span>${esc(n)}</span><span class="hint">new, a copy of ${esc(st.fxDoc[n].copy)}</span></div>`).join('')}</div>
      ${st.fxEntry && e ? `<div class="snd-detail"><b>${esc(st.fxEntry)}</b>
        ${Object.entries(e).filter(([k]) => k !== 'num_components').map(([k, v]) => `<label class="sky-row"><span title="${esc(k)}">${esc(k)}</span>
          <input data-fxk="${esc(k)}" value="${esc(k in edits ? (edits[k] ?? '') : v)}" class="${k in edits ? 'changed' : ''}"></label>`).join('')}
        <div class="buttons">${st.fx[st.fxEntry] ? `<input id="fx-copy-name" placeholder="new entry's name"><button id="fx-copy">Copy as a new entry</button>` : `<button id="fx-drop">Remove this new entry</button>`}
          ${edits && Object.keys(edits).some((k) => k !== 'copy') ? '<button id="fx-reset">Back to the game\'s</button>' : ''}</div></div>` : ''}
      <div class="buttons sticky-save"><button id="fx-save" ${st.fxDirty ? '' : 'disabled'}>Save effects.json</button></div>`;
    bind();
  }

  function bind() {
    $('snd-close').onclick = () => show(false);
    $('snd-bank').onchange = () => loadBank($('snd-bank').value).catch((e) => status(e.message, true));
    const f = $('snd-filter');
    if (f) f.oninput = () => { st.filter = f.value; const at = f.selectionStart; draw(); $('snd-filter').focus(); $('snd-filter').setSelectionRange(at, at); };
    box.querySelectorAll('[data-cue]').forEach((el) => el.onclick = () => { st.cue = el.dataset.cue; draw(); });
    box.querySelectorAll('[data-play]').forEach((b) => b.onclick = () => {
      audio.src = `/api/sound/cue/${encodeURIComponent(st.bank)}/${encodeURIComponent(st.cue)}/${b.dataset.play}.wav?${Date.now()}`;
      audio.play().catch((e) => status(`Can't play it: ${e.message}`, true));
    });
    const stop = $('snd-stop');
    if (stop) stop.onclick = () => audio.pause();
    const saveSound = async (body, then) => {
      try { const r = await post('/api/sound/save', {bank: st.bank, ...body}); status(`Saved ${r.file} (built into the bank with the mod)`); await loadBank(st.bank); if (then) then(); }
      catch (e) { status(e.message, true); }
    };
    const rep = $('snd-replace');
    if (rep) rep.onchange = async () => rep.files[0] && saveSound({cue: st.cue, kind: 'replace', wav: await b64(rep.files[0])});
    const cp = $('snd-copy');
    if (cp) cp.onclick = () => { const n = $('snd-copy-name').value.trim(); saveSound({cue: n, kind: 'like', like: st.cue}, () => { st.cue = n; draw(); }); };
    const tf = $('snd-track-file');
    if (tf) tf.onchange = async () => tf.files[0] && saveSound({cue: st.cue, kind: 'like', track: +$('snd-track').value || 1, wav: await b64(tf.files[0])});
    const nf = $('snd-new-file');
    if (nf) nf.onchange = async () => nf.files[0] && saveSound({cue: $('snd-new-name').value.trim(), kind: 'new', wav: await b64(nf.files[0])});

    const ff = $('fx-filter');
    ff.oninput = () => { st.fxFilter = ff.value; const at = ff.selectionStart; draw(); $('fx-filter').focus(); $('fx-filter').setSelectionRange(at, at); };
    box.querySelectorAll('[data-fx]').forEach((el) => el.onclick = () => { st.fxEntry = el.dataset.fx; draw(); });
    const fxEdited = () => { st.fxDirty = true; $('fx-save').disabled = false; };
    box.querySelectorAll('[data-fxk]').forEach((inp) => inp.onchange = () => {
      const k = inp.dataset.fxk, base = st.fx[st.fxEntry];
      const d = st.fxDoc[st.fxEntry] = st.fxDoc[st.fxEntry] || {};
      const v = inp.value.trim();
      const val = v !== '' && Number.isFinite(+v) && base && /^-?\d/.test(String(base[k] ?? '')) ? +v : v;
      if (base && String(base[k]) === String(v)) delete d[k]; else d[k] = val;
      if (!Object.keys(d).length) delete st.fxDoc[st.fxEntry];
      fxEdited(); draw();
    });
    const fc = $('fx-copy');
    if (fc) fc.onclick = () => {
      const n = $('fx-copy-name').value.trim();
      if (!/^[A-Za-z0-9_]+$/.test(n)) return status('Name new entries with letters, digits and _', true);
      if (st.fx[n] || st.fxDoc[n]) return status(`There's already an entry ${n}`, true);
      st.fxDoc[n] = {copy: st.fxEntry};
      st.fxEntry = n;
      fxEdited(); draw();
    };
    const fd = $('fx-drop');
    if (fd) fd.onclick = () => { delete st.fxDoc[st.fxEntry]; st.fxEntry = ''; fxEdited(); draw(); };
    const fr = $('fx-reset');
    if (fr) fr.onclick = () => {
      const d = st.fxDoc[st.fxEntry];
      if (d && d.copy) st.fxDoc[st.fxEntry] = {copy: d.copy}; else delete st.fxDoc[st.fxEntry];
      fxEdited(); draw();
    };
    $('fx-save').onclick = async () => {
      try { const r = await post('/api/effects/save', {doc: st.fxDoc}); status(`Saved ${r.entries} entries to ${r.file}`); st.fxDirty = false; draw(); }
      catch (e) { status(`Not saved: ${e.message}`, true); }
    };
  }

  function show(on) {
    box.hidden = !on;
    $('sound-mode').classList.toggle('on', on);
    if (!on) audio.pause();
    if (on && !st.fx) load().catch((e) => status(e.message, true));
    else if (on) draw();
  }
  $('sound-mode').onclick = () => show(box.hidden);
  return {show};
}
