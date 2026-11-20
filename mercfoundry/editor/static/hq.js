// HQs & medevac: a new map's faction HQs (flags on the map, guards and their
// interiors), its medevac point and the HQ respawn points (maps/<map>.json
// "hqs", "interiors", "medevac", "hq_respawn"), checked by the build's own
// code when saved (content.py).

const FACTIONS = ['allies', 'china', 'mafia', 'sk'];
const GAME_NAMES = {allies: 'Allies', china: 'China', mafia: 'Mafia', sk: 'South Korea'};

export function createHq(ctx) {
  const {$, status, post, viewCentre, lookAt, mapName, remember} = ctx;
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const st = {map: null, data: null, keys: null, dirty: false};
  const box = document.createElement('div');
  box.id = 'hq';
  box.hidden = true;
  $('panel').prepend(box);                // first in the panel, above the view's hints

  const name = (f) => (st.data && st.data.faction_names && st.data.faction_names[f]) || GAME_NAMES[f];
  const num = (v) => (Number.isFinite(+v) ? Math.round(+v * 100) / 100 : 0);
  const posText = (p) => (p ? p.map((v) => num(v).toFixed(1)).join(', ') : 'not set');

  async function load() {
    st.map = mapName();
    const r = await fetch(`/api/mapkeys/${encodeURIComponent(st.map)}`);
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
    st.data = d;
    st.keys = d.new ? JSON.parse(JSON.stringify(d.keys)) : null;
    st.dirty = false;
    draw();
  }

  function edited() {
    st.dirty = true;
    $('hq-save').disabled = false;
    $('hq-note').textContent = 'Not saved yet.';
  }

  // A point on the map: x, y, z and a heading, set from the view's centre.
  function spotRow(label, spot, id, opts = {}) {
    const p = spot && (spot.position || spot);
    const isArr = Array.isArray(spot);
    return `<div class="hq-spot" data-spot="${id}">
      <span class="hq-label">${esc(label)}</span>
      <span class="hq-pos ${p ? '' : 'hint'}">${esc(posText(Array.isArray(p) ? p : null))}${!isArr && spot && spot.heading != null ? `, facing ${num(spot.heading)}°` : ''}</span>
      <span class="buttons">
        <button data-here="${id}" title="Put it at the middle of the view">Here</button>
        ${p ? `<button data-look="${id}" title="Look at it">Look</button>` : ''}
        ${!opts.noHeading && p ? `<input type="number" step="5" data-heading="${id}" value="${num(spot.heading || 0)}" title="Heading in degrees (0 faces +z, as the editor's arrows)">` : ''}
        ${p ? `<button data-clear="${id}" title="Remove it">&times;</button>` : ''}
      </span></div>`;
  }

  // the value behind a spot id: [object, key, kind]
  function slot(id) {
    const k = st.keys;
    const [kind, f] = id.split(':');
    if (kind === 'medevac') return [k, 'medevac', 'spot'];
    if (kind === 'respawn') { k.hq_respawn = k.hq_respawn || {}; return [k.hq_respawn, f, 'spot']; }
    if (kind === 'flag') { k.hqs = k.hqs || {}; return [k.hqs, f, 'point']; }
    if (kind === 'guard' || kind === 'room') {
      k.interiors = k.interiors || {};
      const i = k.interiors[f] = k.interiors[f] || {};
      return [i, kind, 'spot'];
    }
    if (kind === 'exit') {
      const i = k.interiors[f];
      i.exit = i.exit || {};
      return [i.exit, 'position', 'point'];
    }
    if (kind === 'camera') {
      const i = k.interiors[f];
      i.camera = i.camera || {};
      return [i.camera, 'position', 'point'];
    }
    return [null];
  }

  function linesText(lines) {
    return (lines || []).map((l) => (typeof l === 'string' ? l : (l.speaker ? `${l.speaker}: ${l.text || ''}` : (l.text || '')))).join('\n');
  }
  function textLines(text, old) {
    // keep a line's seconds and sound when its text is unchanged
    const before = new Map((old || []).filter((l) => typeof l !== 'string').map((l) => [linesText([l]), l]));
    return text.split('\n').map((s) => s.trim()).filter(Boolean).map((s) => before.get(s) || s);
  }

  function interiorForm(f, i) {
    const g = i.guard || {}, ex = i.exit || {};
    const bribe = i.bribe === undefined ? true : i.bribe;
    return `<details class="hq-int" ${st.openInt === f ? 'open' : ''} data-int="${f}"><summary>${esc(name(f))}'s interior</summary>
      <p class="hint">The guard is the way in; inside, the leaders stand, the greeting and the contract's briefing play,
        the second in command takes the contract, and the exit takes you back.</p>
      ${spotRow('Guard', g, `guard:${f}`)}
      <label class="sky-row"><span>Name</span><input data-int-k="guard.name" value="${esc(g.name || '')}" placeholder="${esc(name(f))} HQ"></label>
      <label class="sky-row"><span>Template</span><input data-int-k="guard.template" list="templates" value="${esc(g.template || '')}" placeholder="template_${f}_bouncer"></label>
      <label class="sky-row"><span>Prompt</span><input data-int-k="guard.prompt" value="${esc(g.prompt || '')}" placeholder="Enter HQ"></label>
      ${spotRow('Room', i.room, `room:${f}`)}
      ${spotRow('Camera', i.camera && i.camera.position ? {position: i.camera.position} : null, `camera:${f}`, {noHeading: true})}
      <p class="hint">Where the camera stands inside (a fixed spot, as retail's briefing shots; it looks at the boss while
        lines play, else at the player). Put it high in a corner, clear of the walls; unset, it stands 1.2 m behind
        and 1.8 m above the room spot.</p>
      <label class="sky-row"><span>Leaders</span><input data-int-k="leaders" value="${esc((i.leaders || []).join(', '))}" placeholder="actor names, the boss first"></label>
      <label class="sky-row"><span>Second in command</span><input data-int-k="starter" value="${esc(i.starter === false ? '' : (i.starter || ''))}" placeholder="the second leader (accepts the contract)"></label>
      <label class="sky-row"><span>Accept prompt</span><input data-int-k="accept_prompt" value="${esc(i.accept_prompt || '')}" placeholder="Accept contract"></label>
      <label class="sky-row"><span>Talk prompt</span><input data-int-k="talk_prompt" value="${esc(i.talk_prompt || '')}" placeholder="Talk"></label>
      <h4>Way out</h4>
      <label class="sky-row"><span>Exit object</span><input data-int-k="exit.object" value="${esc(ex.object || '')}" placeholder="an actor, or set a point below"></label>
      ${spotRow('Exit point', ex.position ? {position: ex.position} : null, `exit:${f}`, {noHeading: true})}
      <label class="sky-row"><span>Prompt / delay / near</span>
        <span class="hq-trio"><input data-int-k="exit.prompt" value="${esc(ex.prompt || '')}" placeholder="Leave">
        <input type="number" data-int-k="exit.delay" value="${ex.delay ?? ''}" placeholder="5 s">
        <input type="number" data-int-k="exit.distance" value="${ex.distance ?? ''}" placeholder="3 m"></span></label>
      <h4>Hostile faction</h4>
      <label class="check"><input type="checkbox" data-int-k="bribe.on" ${bribe !== false ? 'checked' : ''}> The guard takes a bribe when the faction is hostile</label>
      <label class="sky-row"><span>Bribe cost</span><input type="number" data-int-k="bribe.cost" value="${bribe && bribe.cost != null ? bribe.cost : ''}" placeholder="the chapter's (25,000 and up)"></label>
      <h4>Lines</h4>
      <label class="field">Greeting on the first visit (one line each; "Speaker: text")<textarea rows="3" data-int-lines="greeting">${esc(linesText(i.greeting))}</textarea></label>
      <p class="hint">Every visit, before the briefing: a greeting by the faction's mood toward the player, as retail's
        (leave one empty to skip it).</p>
      <label class="field">Friendly<textarea rows="2" data-int-lines="greetings.friendly">${esc(linesText((i.greetings || {}).friendly))}</textarea></label>
      <label class="field">Neutral<textarea rows="2" data-int-lines="greetings.neutral">${esc(linesText((i.greetings || {}).neutral))}</textarea></label>
      <label class="field">Unfriendly (and hostile, after a bribe)<textarea rows="2" data-int-lines="greetings.unfriendly">${esc(linesText((i.greetings || {}).unfriendly))}</textarea></label>
      <label class="field">Nags while the contract waits (one per line: after 4 s, then every 8 s)<textarea rows="3" data-int-lines="nags">${esc(linesText(i.nags))}</textarea></label>
      <label class="field">With no contract<textarea rows="2" data-int-lines="idle">${esc(linesText(i.idle))}</textarea></label>
      <h4>Atmosphere</h4>
      <p class="hint">Inside, the music stops (as retail's HQs). A room ambience plays if set; it stops on leaving.</p>
      <label class="sky-row"><span>Room ambience</span><input data-int-k="ambience" value="${esc(i.ambience || '')}" placeholder="a looping cue, e.g. ambient.hq_un_war"></label>
      <label class="check" title="Retail hides the HUD inside because its lines are voiced. These lines are on-screen messages and the rings are HUD markers: hide it only if every line has a sound."><input type="checkbox" data-int-k="hide_hud" ${i.hide_hud ? 'checked' : ''}> Hide the HUD inside (only with voiced lines)</label>
      <h4>The guard</h4>
      <label class="check" title="Retail's bouncers nod at you when you're within 15 m: friendly, neutral or unfriendly by the faction's mood (needs a bouncer-like character)"><input type="checkbox" data-int-k="guard_moods" ${(i.guard_moods ?? /_bouncer$/i.test(g.template || `template_${f}_bouncer`)) ? 'checked' : ''}> Mood gestures when the player comes near</label>
      <label class="check" title="Before going in, the guard says one of the faction's retail bouncer lines (allies, sk, china, mafia)"><input type="checkbox" data-int-k="banter.retail" ${i.banter === true ? 'checked' : ''}> Says a retail bouncer line before you go in</label>
      ${i.banter === true ? '' : `<label class="sky-row"><span>Or these cues</span><input data-int-k="banter.cues" value="${esc(Array.isArray(i.banter) ? i.banter.join(', ') : '')}" placeholder="bank.cue, bank.cue (one is picked)"></label>`}
      <div class="buttons"><button data-int-remove="${f}">Remove this interior</button></div>
    </details>`;
  }

  function draw() {
    if (box.hidden) return;
    const d = st.data;
    let body = `<div class="panel-head"><h2>HQs &amp; medevac</h2><button id="hq-close">Close</button></div>`;
    if (!d) { box.innerHTML = body + '<p class="hint">Loading...</p>'; bindClose(); return; }
    if (!d.new) {
      box.innerHTML = body + `<p class="hint">These are for a mod's own new map: open one in the Map list (or make one
        with the Map tool). The game's maps keep their HQs.</p>`;
      bindClose();
      return;
    }
    const k = st.keys;
    const gen = d.generated || [];
    body += gen.length ? `<p class="warn">${esc(gen.join(', '))} ${gen.length > 1 ? 'are' : 'is'} written by this mod's own tools
      (mod.json "generated"): edits here are replaced the next time they run.</p>` : '';
    body += `<h3>Medevac point</h3><p class="hint">Where the game puts the player back on this map: a medevac
      from the pause menu, a contract given up after dying, free roam starting (the game's MASH on SW).</p>
      ${spotRow('MASH', k.medevac, 'medevac')}
      <h3>HQ respawn points</h3><p class="hint">Where a death during a faction's contract puts the player.</p>
      ${FACTIONS.map((f) => spotRow(name(f), (k.hq_respawn || {})[f], `respawn:${f}`)).join('')}
      <h3>HQ flags</h3><p class="hint">Each faction's HQ flag on the radar and PDA map.</p>
      ${FACTIONS.map((f) => spotRow(name(f), (k.hqs || {})[f] || null, `flag:${f}`, {noHeading: true})).join('')}
      <h3>HQ interiors</h3>
      ${FACTIONS.map((f) => ((k.interiors || {})[f] ? interiorForm(f, k.interiors[f])
        : `<div class="hq-spot"><span class="hq-label">${esc(name(f))}</span><span class="hint">no interior</span>
           <span class="buttons"><button data-int-add="${f}">Add one</button></span></div>`)).join('')}
      <details><summary>Raw JSON (everything above, as it's saved)</summary>
        <textarea id="hq-raw" rows="10" spellcheck="false">${esc(JSON.stringify(k, null, 1))}</textarea>
        <div class="buttons"><button id="hq-raw-apply">Use this</button></div></details>
      <div class="buttons sticky-save"><button id="hq-save" ${st.dirty ? '' : 'disabled'}>Save HQs</button>
        <span id="hq-note" class="hint">${st.dirty ? 'Not saved yet.' : ''}</span></div>`;
    box.innerHTML = body;
    bind();
  }

  function bindClose() { $('hq-close').onclick = () => show(false); }

  function bind() {
    bindClose();
    box.querySelectorAll('[data-here]').forEach((b) => b.onclick = () => {
      const id = b.dataset.here, [obj, key, kind] = slot(id);
      const p = viewCentre().map(num);
      if (id.startsWith('camera:')) p[1] = num(p[1] + 1.8);      // the view's centre is on the floor: head height
      if (kind === 'point') obj[key] = p;
      else obj[key] = {...(obj[key] || {}), position: p, heading: (obj[key] && obj[key].heading) || 0};
      edited(); draw();
    });
    box.querySelectorAll('[data-look]').forEach((b) => b.onclick = () => {
      const [obj, key] = slot(b.dataset.look);
      const v = obj[key];
      const p = Array.isArray(v) ? v : v && v.position;
      if (p) lookAt(p, 60);
    });
    box.querySelectorAll('[data-heading]').forEach((inp) => inp.onchange = () => {
      const [obj, key] = slot(inp.dataset.heading);
      if (obj[key]) obj[key].heading = num(inp.value);
      edited();
    });
    box.querySelectorAll('[data-clear]').forEach((b) => b.onclick = () => {
      const [obj, key] = slot(b.dataset.clear);
      delete obj[key];
      edited(); draw();
    });
    box.querySelectorAll('[data-int-add]').forEach((b) => b.onclick = () => {
      const f = b.dataset.intAdd;
      st.keys.interiors = st.keys.interiors || {};
      const c = viewCentre().map(num);
      st.keys.interiors[f] = {guard: {position: c, heading: 0}, room: {position: [c[0], c[1], c[2] + 20], heading: 180},
                              leaders: [], exit: {prompt: 'Leave'}, greeting: [], idle: ['Nothing for you right now.']};
      st.openInt = f;
      edited(); draw();
    });
    box.querySelectorAll('[data-int-remove]').forEach((b) => b.onclick = () => {
      if (!confirm(`Remove ${name(b.dataset.intRemove)}'s interior?`)) return;
      delete st.keys.interiors[b.dataset.intRemove];
      edited(); draw();
    });
    box.querySelectorAll('details.hq-int').forEach((d) => d.ontoggle = () => { if (d.open) st.openInt = d.dataset.int; });
    box.querySelectorAll('[data-int-k]').forEach((inp) => inp.onchange = () => {
      const f = inp.closest('[data-int]').dataset.int;
      const i = st.keys.interiors[f];
      const path = inp.dataset.intK;
      const val = inp.type === 'checkbox' ? inp.checked : inp.value.trim();
      if (path === 'leaders') i.leaders = val ? val.split(',').map((s) => s.trim()).filter(Boolean) : [];
      else if (path === 'starter') { if (val) i.starter = val; else delete i.starter; }
      else if (path === 'bribe.on') i.bribe = val ? (typeof i.bribe === 'object' ? i.bribe : true) : false;
      else if (path === 'bribe.cost') i.bribe = val ? {cost: Math.max(0, Math.round(+val))} : true;
      else if (path === 'hide_hud') { if (val) i.hide_hud = true; else delete i.hide_hud; }
      else if (path === 'guard_moods') i.guard_moods = !!val;
      else if (path === 'banter.retail') {
        if (val) i.banter = true; else if (i.banter === true) delete i.banter;
        draw();
      } else if (path === 'banter.cues') {
        const cues = val ? val.split(',').map((s) => s.trim()).filter(Boolean) : [];
        if (cues.length) i.banter = cues; else if (Array.isArray(i.banter)) delete i.banter;
      } else {
        const [a, b] = path.split('.');
        const target = b ? (i[a] = i[a] || {}) : i;
        const key = b || a;
        const v = inp.type === 'number' ? (val === '' ? null : +val) : val;
        if (v === '' || v === null) delete target[key]; else target[key] = v;
      }
      edited();
    });
    box.querySelectorAll('[data-int-lines]').forEach((ta) => ta.onchange = () => {
      const f = ta.closest('[data-int]').dataset.int;
      const i = st.keys.interiors[f];
      const [a, b] = ta.dataset.intLines.split('.');          // greetings.friendly: a mood's lines
      const target = b ? (i[a] = i[a] || {}) : i;
      const key = b || a;
      const lines = textLines(ta.value, target[key]);
      if (lines.length || key === 'greeting' || key === 'idle') target[key] = lines; else delete target[key];
      if (b && !Object.keys(target).length) delete i[a];
      edited();
    });
    $('hq-raw-apply').onclick = () => {
      try { st.keys = JSON.parse($('hq-raw').value); edited(); draw(); } catch (e) { status(`Not JSON: ${e.message}`, true); }
    };
    $('hq-save').onclick = save;
  }

  async function save() {
    const changes = {};
    for (const k of ['hqs', 'interiors', 'medevac', 'hq_respawn']) {
      const v = st.keys[k];
      changes[k] = v && (Array.isArray(v) ? v.length : Object.keys(v).length) ? v : null;
    }
    try {
      const r = await post('/api/mapkeys/save', {map: st.map, changes});
      st.dirty = false;
      status(`Saved the HQs to ${r.file}`);
      await load();
    } catch (e) {
      $('hq-note').textContent = e.message;
      status(`Not saved: ${e.message}`, true);
    }
  }

  function show(on) {
    box.hidden = !on;
    $('hq-mode').classList.toggle('on', on);
    if (on && (st.map !== mapName() || !st.data)) load().catch((e) => status(e.message, true));
    else if (on) draw();
  }
  $('hq-mode').onclick = () => show(box.hidden);
  return {show, load, isDirty: () => st.dirty};
}
