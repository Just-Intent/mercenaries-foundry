// Text & mail: the mod's own text (text/*.json: the game's strings with new
// words, everywhere or only on a new map, "map"), found by searching the
// game's text, and the mod's welcome mail (mod.json "welcome_mail").

export function createText(ctx) {
  const {$, status, post} = ctx;
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const st = {data: null, cur: 0, dirty: new Set(), mailDirty: false, found: [], filter: ''};
  const box = document.createElement('div');
  box.id = 'text';
  box.hidden = true;
  $('panel').prepend(box);

  async function load() {
    const r = await fetch('/api/text');
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
    st.data = d;
    st.mail = d.mail ? {...d.mail} : null;
    st.dirty.clear();
    st.mailDirty = false;
    if (st.cur >= d.files.length) st.cur = 0;
    draw();
  }

  const file = () => st.data && st.data.files[st.cur];

  function draw() {
    if (box.hidden) return;
    const d = st.data;
    let h = `<div class="panel-head"><h2>Text &amp; mail</h2><button id="text-close">Close</button></div>`;
    if (!d) { box.innerHTML = h + '<p class="hint">Loading...</p>'; bind(); return; }
    const m = st.mail;
    h += `<details ${m ? 'open' : ''}><summary><b>Welcome mail</b> <span class="hint">${m ? esc(m.subject || '') : 'none'}</span></summary>
      <p class="hint">Posted once per save when free roam first starts on its map (SW unless set).</p>
      <label class="check"><input type="checkbox" id="mail-on" ${m ? 'checked' : ''}> The mod sends one</label>
      ${m ? `<label class="sky-row"><span>From</span><input id="mail-sender" value="${esc(m.sender || '')}" placeholder="mail.senders.techgal, or a name"></label>
        <label class="sky-row"><span>Subject</span><input id="mail-subject" value="${esc(m.subject || '')}"></label>
        <label class="field">Body<textarea id="mail-body" rows="5">${esc(m.body || '')}</textarea></label>
        <label class="sky-row"><span>Map</span><select id="mail-map"><option value="">sw</option><option ${m.map === 'nw' ? 'selected' : ''}>nw</option></select></label>` : ''}
      <div class="buttons"><button id="mail-save" ${st.mailDirty ? '' : 'disabled'}>Save the mail</button></div></details>
      <h3>The game's text, reworded</h3>
      <p class="hint">Each file gives game strings new words: everywhere, or only while one of the mod's new maps is
        loaded (with Foundry Console). Search the game's text to find a string's key.</p>
      <div class="tabs">${d.files.map((f, i) => `<button data-tab="${i}" class="${i === st.cur ? 'on' : ''}">${esc(f.file.replace('text/', ''))}${st.dirty.has(i) ? ' *' : ''}</button>`).join('')}
        <button id="text-new">+ File</button></div>`;
    const f = file();
    if (f && !f.error) {
      const keys = Object.keys(f.strings).filter((k) => !st.filter || k.toLowerCase().includes(st.filter) || String(f.strings[k]).toLowerCase().includes(st.filter));
      h += `${f.generated === true ? '<p class="warn">This file is written by the mod\'s own tools: edits here are replaced the next time they run.</p>' : ''}
        <label class="sky-row"><span>Where</span><select id="text-map"><option value="">everywhere</option>
          ${d.maps.map((mm) => `<option value="${esc(mm)}" ${f.map === mm ? 'selected' : ''}>only on ${esc(mm)}</option>`).join('')}</select></label>
        <label class="sky-row"><span>Show</span><input id="text-filter" value="${esc(st.filter)}" placeholder="only keys or words with..."></label>
        <p class="hint">${Object.keys(f.strings).length} strings${st.filter ? `, ${keys.length} shown` : ''}${keys.length > 200 ? ' (the first 200)' : ''}</p>
        ${keys.slice(0, 200).map((k) => `<div class="text-row" data-key="${esc(k)}"><code>${esc(k)}</code>
          <button data-del title="Back to the game's">&times;</button>
          <textarea rows="2">${esc(f.strings[k])}</textarea></div>`).join('')}
        <div class="buttons sticky-save"><button id="text-save" ${st.dirty.has(st.cur) ? '' : 'disabled'}>Save ${esc(f.file)}</button></div>`;
    } else if (f && f.error) {
      h += `<p class="warn">${esc(f.file)}: ${esc(f.error)}</p>`;
    }
    h += `<h3>Find the game's text</h3>
      <input id="text-search" type="search" placeholder="a key or words, e.g. allies or Pyongyang">
      <div id="text-found">${st.found.map(([k, v]) => `<div class="text-hit" data-add="${esc(k)}" title="Add it to ${esc(f ? f.file : 'a new file')}"><code>${esc(k)}</code><span>${esc(v)}</span></div>`).join('')}</div>`;
    box.innerHTML = h;
    bind();
  }

  function edited() { st.dirty.add(st.cur); const b = $('text-save'); if (b) b.disabled = false; }

  function bind() {
    $('text-close').onclick = () => show(false);
    if (!st.data) return;
    const mailOn = $('mail-on');
    mailOn.onchange = () => { st.mail = mailOn.checked ? {sender: '', subject: '', body: ''} : null; st.mailDirty = true; draw(); };
    for (const [id, k] of [['mail-sender', 'sender'], ['mail-subject', 'subject'], ['mail-body', 'body'], ['mail-map', 'map']]) {
      const el = $(id);
      if (el) el.onchange = () => { st.mail[k] = el.value.trim(); st.mailDirty = true; $('mail-save').disabled = false; };
    }
    $('mail-save').onclick = async () => {
      try { await post('/api/mail/save', {mail: st.mail}); status('Saved the welcome mail to mod.json'); await load(); }
      catch (e) { status(`Not saved: ${e.message}`, true); }
    };
    box.querySelectorAll('[data-tab]').forEach((b) => b.onclick = () => { st.cur = +b.dataset.tab; draw(); });
    $('text-new').onclick = () => {
      const name = (prompt('The new file\'s name (letters, digits, _ and -):', 'my_text') || '').trim();
      if (!name) return;
      st.data.files.push({file: `text/${name.replace(/\.json$/i, '')}.json`, map: null, strings: {}});
      st.cur = st.data.files.length - 1;
      edited(); draw();
    };
    const f = file();
    if (f && !f.error) {
      $('text-map').onchange = () => { f.map = $('text-map').value || null; edited(); };
      $('text-filter').oninput = () => { st.filter = $('text-filter').value.toLowerCase(); const at = $('text-filter').selectionStart; draw(); $('text-filter').focus(); $('text-filter').setSelectionRange(at, at); };
      box.querySelectorAll('.text-row').forEach((r) => {
        const k = r.dataset.key;
        r.querySelector('textarea').onchange = (e) => { f.strings[k] = e.target.value; edited(); };
        r.querySelector('[data-del]').onclick = () => { delete f.strings[k]; edited(); draw(); };
      });
      $('text-save').onclick = async () => {
        try {
          const r = await post('/api/text/save', {file: f.file, map: f.map, strings: f.strings});
          status(`Saved ${r.count} strings to ${r.file}`);
          const keep = st.cur; await load(); st.cur = keep; draw();
        } catch (e) { status(`Not saved: ${e.message}`, true); }
      };
    }
    let timer = null;
    $('text-search').oninput = () => {
      clearTimeout(timer);
      const q = $('text-search').value;
      timer = setTimeout(async () => {
        try {
          const r = await fetch(`/api/text/search?q=${encodeURIComponent(q)}`);
          st.found = await r.json();
          const keep = q;
          draw();
          $('text-search').value = keep; $('text-search').focus();
        } catch (e) { status(e.message, true); }
      }, 300);
    };
    box.querySelectorAll('[data-add]').forEach((el) => el.onclick = () => {
      let tgt = file();
      if (!tgt || tgt.error) {
        st.data.files.push({file: 'text/text.json', map: null, strings: {}});
        st.cur = st.data.files.length - 1;
        tgt = file();
      }
      const hit = st.found.find(([k]) => k === el.dataset.add);
      if (!(hit[0] in tgt.strings)) tgt.strings[hit[0]] = hit[1];
      st.filter = '';
      edited(); draw();
      status(`${hit[0]} added: give it its new words`);
    });
  }

  function show(on) {
    box.hidden = !on;
    $('text-mode').classList.toggle('on', on);
    if (on && !st.data) load().catch((e) => status(e.message, true));
    else if (on) draw();
  }
  $('text-mode').onclick = () => show(box.hidden);
  return {show};
}
