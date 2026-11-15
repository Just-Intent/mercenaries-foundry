// Mods: which of the game's source mods the editor saves to (shown in the
// toolbar), switching to another, making one, its name, description and
// whether the launcher builds it, and deleting one (moved to the game's
// foundry-trash folder, not erased).

export function createMods(ctx) {
  const {$, status, post, isDirty, afterSwitch} = ctx;
  const st = {data: null};
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  async function load() {
    const r = await fetch('/api/mods');
    st.data = await r.json();
    if (!r.ok) throw new Error(st.data.error || r.statusText);
    draw();
  }

  function show(on) {
    $('mods').hidden = !on;
    $('mod').classList.toggle('on', on);
    if (on) load().catch((e) => status(e.message, true));
  }

  function draw() {
    const d = st.data;
    if (!d || $('mods').hidden) return;
    const cur = d.mods.find((m) => m.current);
    const rows = d.mods.map((m) => `
      <div class="mod-row${m.current ? ' mod-current' : ''}">
        <div><strong>${esc(m.name)}</strong> <span class="hint">mods/${esc(m.folder)}</span>
          ${m.current ? '<span class="hint">(saving here)</span>' : ''}</div>
        <div class="buttons">
          ${m.current ? '' : `<button data-switch="${esc(m.folder)}" title="Edit and save to this mod">Edit this mod</button>`}
          ${d.game ? `<label class="check"><input type="checkbox" data-enable="${esc(m.folder)}"${m.enabled ? ' checked' : ''}> Built by the launcher</label>` : ''}
          ${m.current || !d.game ? '' : `<button data-delete="${esc(m.folder)}" title="Move it to the game's foundry-trash folder">Delete</button>`}
        </div>
      </div>`).join('');
    $('mods-body').innerHTML = `
      <p class="hint">Everything you save goes into the mod marked "saving here".</p>
      ${rows}
      ${cur ? `<h3>This mod</h3>
        <label class="field">Name <input id="mods-name" value="${esc(cur.name)}"></label>
        <label class="field">Description <input id="mods-desc" value="${esc(cur.description)}"></label>
        <div class="buttons"><button id="mods-save" data-folder="${esc(cur.folder)}">Save name and description</button></div>` : ''}
      ${d.game ? `<h3>New mod</h3>
        <label class="field">Name <input id="mods-new-name" placeholder="My mod"></label>
        <label class="field">Description <input id="mods-new-desc" placeholder="(optional)"></label>
        <div class="buttons"><button id="mods-create" title="A new mod in the game's mods folder, built by the launcher; the editor saves to it">Make the mod</button></div>`
        : '<p class="hint">The editor was started on a mod outside a game\'s mods folder, so it has just this one.</p>'}`;
  }

  async function switchTo(folder, make) {
    if (isDirty() && !confirm('Discard unsaved changes?')) return;
    try {
      st.data = await make();
      draw();
      await afterSwitch();
      const cur = st.data.mods.find((m) => m.current);
      status(`Editing ${cur ? cur.name : folder}: saves go into mods/${cur ? cur.folder : folder}`);
    } catch (e) { status(e.message, true); }
  }

  $('mods').addEventListener('click', async (e) => {
    const t = e.target;
    if (t.dataset.switch) switchTo(t.dataset.switch, () => post('/api/mods/switch', {folder: t.dataset.switch}));
    if (t.dataset.delete) {
      if (!confirm(`Delete the mod in mods/${t.dataset.delete}? It's moved to the game's foundry-trash folder (move it back to restore it).`)) return;
      try {
        st.data = await post('/api/mods/delete', {folder: t.dataset.delete});
        draw();
        status(`Moved mods/${t.dataset.delete} to ${st.data.moved_to}`);
      } catch (err) { status(err.message, true); }
    }
    if (t.id === 'mods-save') {
      try {
        st.data = await post('/api/mods/update', {folder: t.dataset.folder, name: $('mods-name').value,
                                                 description: $('mods-desc').value});
        draw();
        afterSwitch(true);
        status('Saved the mod\'s name and description (mod.json)');
      } catch (err) { status(err.message, true); }
    }
    if (t.id === 'mods-create') {
      const name = $('mods-new-name').value.trim();
      if (!name) return status('Give the new mod a name', true);
      switchTo(name, () => post('/api/mods/create', {name, description: $('mods-new-desc').value}));
    }
  });
  $('mods').addEventListener('change', async (e) => {
    const folder = e.target.dataset.enable;
    if (!folder) return;
    try {
      st.data = await post('/api/mods/update', {folder, enabled: e.target.checked});
      draw();
      status(`mods/${folder} is ${e.target.checked ? 'built' : 'not built'} by the launcher now`);
    } catch (err) { status(err.message, true); }
  });

  $('mod').onclick = () => show($('mods').hidden);
  $('mods-close').onclick = () => show(false);
  return {show, load};
}
