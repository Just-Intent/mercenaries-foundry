// Models: the mod's own models (models/<name>.new.json) and their build
// settings. A new model (a .glb on a game model's skeleton) gets a collision
// box fitted to each of its pieces unless it keeps its base's own collision:
// right for aircraft, whose base has tuned boxes (a box fitted round a rotor
// held Gaza's extraction Black Hawk off the ground and its passengers out).
// Its shadow is the base's shadow mesh, or none.

export function createModels(ctx) {
  const {$, status, post} = ctx;
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const st = {specs: null, filter: ''};
  const box = document.createElement('div');
  box.id = 'models';
  box.hidden = true;
  $('panel').prepend(box);

  async function load() {
    const r = await fetch('/api/modelspecs');
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
    st.specs = d;
    draw();
  }

  // a base's kind, for the hint: aircraft want the base's collision
  const aircraft = (base) => /_veh_(apache|blackhawk|chinook|littlebird|oh6littlebird|mi2hoplite|hind|hokum|a10|cobra|seahawk|support|.*heli)/i.test(base || '');

  function row(s) {
    const air = aircraft(s.base);
    const advice = s.kind === 'new' && air && s.collision !== 'base'
      ? '<span class="warn" title="An aircraft keeps its base\'s tuned collision best: fitted boxes round a rotor make it hover, block landing and keep passengers out">aircraft: keep the base\'s collision</span>' : '';
    return `<div class="mdl-row" data-model="${esc(s.name)}">
      <div class="mdl-name"><b>${esc(s.name)}</b> <span class="hint">${s.kind === 'copy' ? 'a copy of' : 'on'} ${esc(s.base)}</span> ${advice}</div>
      <div class="mdl-opts">
        ${s.kind === 'new'
          ? `<label class="check" title="Keep the base model's own collision shapes, whole, instead of a box fitted to each new piece. For aircraft and anything whose base was hand-tuned."><input type="checkbox" data-coll ${s.collision === 'base' ? 'checked' : ''}> Keep the base's collision</label>`
          : '<span class="hint">keeps its base\'s collision</span>'}
        <label class="check" title="Cast the base model's shadow (a vehicle's shadow is its base's outline; turn it off when that outline is wrong)"><input type="checkbox" data-shadow ${s.shadow ? 'checked' : ''}> Shadow</label>
      </div></div>`;
  }

  function draw() {
    if (box.hidden) return;
    let html = `<div class="panel-head"><h2>Models</h2><button id="models-close">Close</button></div>
      <p class="hint">This mod's own models and how the build makes them. A new model (your .glb on a game model's
        skeleton) gets a collision box fitted round each piece; aircraft should keep their base's collision instead.
        Changes save at once and show in game after the next build.</p>`;
    if (!st.specs) {
      box.innerHTML = html + '<p class="hint">Loading...</p>';
      bind();
      return;
    }
    const q = st.filter.toLowerCase();
    const list = st.specs.filter((s) => !q || s.name.toLowerCase().includes(q) || s.base.toLowerCase().includes(q));
    const needs = st.specs.filter((s) => s.kind === 'new' && aircraft(s.base) && s.collision !== 'base').length;
    html += `<input id="models-filter" type="search" value="${esc(st.filter)}" placeholder="find a model or its base, e.g. blackhawk">
      ${needs ? `<p class="warn">${needs} aircraft model(s) still have fitted collision.</p>` : ''}
      <div class="mdl-list">${list.map(row).join('') || '<p class="hint">No models of the mod\'s own.</p>'}</div>`;
    box.innerHTML = html;
    bind();
  }

  function bind() {
    $('models-close').onclick = () => show(false);
    const f = $('models-filter');
    if (f) f.oninput = () => {
      st.filter = f.value;
      const at = f.selectionStart;
      draw();
      $('models-filter').focus();
      $('models-filter').setSelectionRange(at, at);
    };
    box.querySelectorAll('.mdl-row').forEach((rowEl) => {
      const name = rowEl.dataset.model;
      const save = async (body, input) => {
        try {
          const r = await post('/api/modelspecs/save', {name, ...body});
          const i = st.specs.findIndex((s) => s.name === name);
          if (i >= 0) st.specs[i] = r.spec;
          status(`Saved ${r.file}: shows in game after the next build`);
          draw();
        } catch (e) {
          input.checked = !input.checked;
          status(e.message, true);
        }
      };
      const c = rowEl.querySelector('[data-coll]');
      if (c) c.onchange = () => save({collision: c.checked ? 'base' : 'fitted'}, c);
      const s = rowEl.querySelector('[data-shadow]');
      if (s) s.onchange = () => save({shadow: s.checked}, s);
    });
  }

  function show(on) {
    box.hidden = !on;
    $('models-mode').classList.toggle('on', on);
    if (on && !st.specs) load().catch((e) => status(e.message, true));
    else if (on) draw();
  }
  $('models-mode').onclick = () => show(box.hidden);
  return {show, load};
}
