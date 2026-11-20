// Limits: the game's limits a map can run into, with the open map's counts
// where the editor has them. Going over most of these doesn't fail the build:
// the game freezes or hangs while loading, so they're worth watching.

const OBJECT_LIMIT = 16384;

export function createLimits(ctx) {
  const {$, stats} = ctx;
  const box = document.createElement('div');
  box.id = 'limits';
  box.hidden = true;
  $('panel').prepend(box);                // first in the panel, above the view's hints

  function bar(n, max) {
    const pct = Math.min(100, Math.round((n / max) * 100));
    const cls = pct >= 95 ? 'bad' : pct >= 80 ? 'warn' : 'ok';
    return `<div class="lim-bar ${cls}"><i style="width:${pct}%"></i></div><span class="hint">${n.toLocaleString()} of ${max.toLocaleString()} (${pct}%)</span>`;
  }

  function draw() {
    if (box.hidden) return;
    const s = stats();
    box.innerHTML = `<div class="panel-head"><h2>Limits</h2><button id="limits-close">Close</button></div>
      <p class="hint">The game's own limits. Past most of them the build still works, but the game freezes or hangs
        while loading, so keep an eye on them as the map grows.</p>
      <h3>Objects on ${s.map}</h3>
      ${bar(s.objects, OBJECT_LIMIT)}
      <p class="hint">Everything placed in the layers the editor shows (${s.placed.toLocaleString()} of them the mod's own,
        ${s.removed.toLocaleString()} removed). The game holds ${OBJECT_LIMIT.toLocaleString()} at once; layers loaded later
        (missions, card scenes) count too while they're loaded.</p>
      <h3>Known limits</h3>
      <ul class="lim-list">
        <li><b>Traffic routes</b>: 1,024 on a map (walkers, cars and drones together).</li>
        <li><b>Names</b>: the name table holds 2,048; every named path and zone uses one. A full table hangs the map's load.</li>
        <li><b>Textures</b>: 512&times;512 at most (larger ones may never find room); a map's textures share about 15 MB of heap.</li>
        <li><b>Models</b>: each piece under about 3,000 vertices, each model under about 128 KB of geometry.</li>
        <li><b>Objectives</b>: 16 at once (HQ flags, marked guards, mission givers and the mission's own).</li>
        <li><b>Long load lists</b>: a layer that loads hundreds of templates slows the map's load; give copies "copy_loads": false.</li>
      </ul>
      <div class="buttons"><button id="limits-refresh">Count again</button></div>`;
    $('limits-close').onclick = () => show(false);
    $('limits-refresh').onclick = draw;
  }

  function show(on) {
    box.hidden = !on;
    $('limits-mode').classList.toggle('on', on);
    if (on) draw();
  }
  $('limits-mode').onclick = () => show(box.hidden);
  return {show, draw};
}
