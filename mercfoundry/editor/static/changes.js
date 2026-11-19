// Changes: the mod's changed files (from its own git repository, when it has
// one), committing and pushing them, and building the game's source mods as
// the launcher does (then a playtest zip, when the toolkit's source is there).
// Files the mod's own tools write (mod.json "generated") are marked: an edit
// to them is overwritten when those tools run again.

export function createChanges(ctx) {
  const {$, status, post} = ctx;
  const st = {data: null, build: null, timer: null, picked: null, message: ''};
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  async function refresh() {
    try {
      const r = await fetch('/api/git');
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.statusText);
      st.data = d;
      const n = d.git ? d.files.length : 0;
      $('changes-open').textContent = n ? `Changes (${n})` : 'Changes';
      $('changes-open').classList.toggle('has', n > 0);
      draw();
    } catch (e) {
      if (!$('changes').hidden) status(e.message, true);
    }
  }

  async function refreshBuild() {
    const r = await fetch('/api/build');
    st.build = await r.json();
    draw();
    clearTimeout(st.timer);
    if (st.build.running) st.timer = setTimeout(refreshBuild, 1500);
    else if (st.build.ok !== undefined && st.build.ok !== null && st.wasRunning) {
      status(st.build.ok ? `Build done${st.build.zip ? `: ${st.build.zip}` : ''}` : 'The build failed: see Changes', !st.build.ok);
    }
    st.wasRunning = st.build.running;
  }

  function genNote(g) {
    if (g === true) return ' title="Written by the mod\'s own tools: an edit here is overwritten when they run again"';
    if (Array.isArray(g)) return ` title="Its ${esc(g.join(', '))} ${g.length > 1 ? 'are' : 'is'} written by the mod's own tools: edits there are overwritten when they run again"`;
    return '';
  }

  function draw() {
    const d = st.data;
    if (!d || $('changes').hidden) return;
    let html = '';
    if (!d.git) {
      html += `<p class="hint">mods/${esc(d.folder)} isn't under git of its own, so there's nothing to commit. To put it
        under git, run <code>git init</code> in its folder (or give it a <code>.git</code> file:
        <code>gitdir: C:/somewhere/mod.git</code>).</p>`;
    } else {
      // keep the ticks across refreshes; new files start ticked
      const known = st.picked || {};
      st.picked = {};
      for (const f of d.files) st.picked[f.path] = f.path in known ? known[f.path] : true;
      const gen = d.files.filter((f) => f.generated);
      html += `<p class="hint">Branch <strong>${esc(d.branch || '?')}</strong>${d.upstream ? ` (tracks ${esc(d.upstream)}${d.ahead ? `, ${d.ahead} to push` : ''}${d.behind ? `, ${d.behind} behind` : ''})` : d.remotes.length ? ' (not pushed yet)' : ' (no remote)'}.
        ${d.last ? `Last commit: ${esc(d.last)}.` : ''} ${d.identity ? `Commits as ${esc(d.identity)}.` : '<strong>No git identity set for it.</strong>'}</p>`;
      if (!d.files.length) html += '<p class="hint">Nothing changed since the last commit.</p>';
      else {
        html += `<div class="buttons"><button id="chg-all">All</button><button id="chg-none">None</button></div>`;
        html += d.files.map((f) => `<label class="chg-row${f.generated ? ' chg-gen' : ''}"${genNote(f.generated)}>
          <input type="checkbox" data-path="${esc(f.path)}"${st.picked[f.path] ? ' checked' : ''}>
          <span class="chg-kind">${esc(f.kind)}</span><span class="chg-path">${esc(f.path)}${f.generated === true ? ' (tools)' : Array.isArray(f.generated) ? ` (tools: ${esc(f.generated.join(', '))})` : ''}</span></label>`).join('');
        if (gen.length) html += `<p class="hint">Orange: written by the mod's own tools (mod.json "generated"). An edit to them
          is overwritten when those tools run again: change the tools' input instead, or keep it in mind.</p>`;
        html += `<label class="field">Message <textarea id="chg-msg" placeholder="What changed">${esc(st.message)}</textarea></label>
          <div class="buttons"><button id="chg-commit">Commit</button></div>`;
      }
      if (d.remotes.length) {
        html += `<div class="buttons"><button id="chg-push"${d.upstream && !d.ahead ? ' disabled' : ''}>Push${d.ahead ? ` (${d.ahead})` : ''}</button></div>`;
      }
    }
    html += '<h3>Build</h3>';
    if (!d.game) html += '<p class="hint">The editor isn\'t in a game\'s mods folder, so there\'s no launcher build to run.</p>';
    else {
      const b = st.build || {};
      html += `<p class="hint">Builds the game's source mods as the launcher does before a modded launch (mod sync).${d.playtest ? ' Then, if you like, a playtest zip for the other PC.' : ''}</p>
        <div class="buttons"><button id="chg-build"${b.running ? ' disabled' : ''}>Build</button>
        ${d.playtest ? `<button id="chg-playtest"${b.running ? ' disabled' : ''}>Build + playtest zip</button>` : ''}</div>`;
      if (b.lines && b.lines.length) {
        html += `<p class="hint">${b.running ? `Running: ${esc(b.step)}...` : b.ok ? 'Done.' : 'Failed.'}${b.zip ? ` Zip: ${esc(b.zip)}` : ''}</p>
          <pre id="chg-log">${esc(b.lines.slice(-80).join('\n'))}</pre>`;
      }
    }
    $('changes-body').innerHTML = html;
    const log = document.getElementById('chg-log');
    if (log) log.scrollTop = log.scrollHeight;
  }

  async function act(label, fn) {
    try {
      status(`${label}...`);
      const d = await fn();
      if (d && d.git !== undefined) st.data = {...st.data, ...d};
      draw();
      return d;
    } catch (e) { status(e.message, true); return null; }
  }

  $('changes').addEventListener('input', (e) => { if (e.target.id === 'chg-msg') st.message = e.target.value; });
  $('changes').addEventListener('change', (e) => {
    const p = e.target.dataset.path;
    if (p) st.picked[p] = e.target.checked;
  });
  $('changes').addEventListener('click', async (e) => {
    const t = e.target;
    if (t.id === 'chg-all' || t.id === 'chg-none') {
      for (const k of Object.keys(st.picked)) st.picked[k] = t.id === 'chg-all';
      draw();
    }
    if (t.id === 'chg-commit') {
      const paths = Object.keys(st.picked).filter((k) => st.picked[k]);
      const d = await act('Committing', () => post('/api/git/commit', {message: st.message, paths}));
      if (d) { st.message = ''; await refresh(); status(`Committed ${d.committed}`); }
    }
    if (t.id === 'chg-push') {
      const d = await act('Pushing', () => post('/api/git/push', {}));
      if (d) { await refresh(); status(`Pushed: ${d.pushed.join(' ')}`); }
    }
    if (t.id === 'chg-build' || t.id === 'chg-playtest') {
      const d = await act('Starting the build', () => post('/api/build', {playtest: t.id === 'chg-playtest'}));
      if (d) { st.build = d; st.wasRunning = true; status('Building (this takes a few minutes for a big mod)...'); refreshBuild(); }
    }
  });

  function show(on) {
    $('changes').hidden = !on;
    $('changes-open').classList.toggle('on', on);
    if (on) { refresh(); refreshBuild().catch(() => {}); }
  }
  $('changes-open').onclick = () => show($('changes').hidden);
  $('changes-close').onclick = () => show(false);
  refresh();
  return {show, refresh};
}
