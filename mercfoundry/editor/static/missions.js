// Missions: contracts made in the editor. A mission is
// missions/<map>_<faction><n>.json (title, description, briefing mail, fee and
// objectives) plus its layer, encounters/<same name>.json, where its squads and
// zones go (the mission loads it). With objectives and no script of the mod's
// own, the build writes the mission's script: main objectives one after
// another, bonus objectives alongside, then the contract completes. A mission
// giver offers it: a contact at the faction's HQ, someone placed on the map,
// or no one (started by the Lua line).
import * as THREE from 'three';

export function createMissions(ctx) {
  const {$, status, setDirty} = ctx;
  const st = {map: null, list: [], next: {}, current: null, dirty: false, start: null,
              giver: {mode: 'hq'}};

  // ---------------------------------------------------------------- placed points
  // Markers you place, drag over the ground and turn with the gizmo: the
  // player's start (a yellow arrow) and a placed mission giver (a cyan figure
  // with an arrow). Each points along +z, the game's forward at heading 0.
  function makeMarker(kind, colour, figure) {
    const g = new THREE.Group();
    const mat = new THREE.MeshLambertMaterial({color: colour});
    const body = figure
      ? new THREE.Mesh(new THREE.CapsuleGeometry(0.45, 1.1, 4, 10), mat)
      : new THREE.Mesh(new THREE.CylinderGeometry(0.25, 0.25, 3, 8), mat);
    body.position.y = figure ? 1.0 : 1.5;
    const arrow = new THREE.Mesh(new THREE.ConeGeometry(figure ? 0.5 : 0.8, figure ? 1.2 : 2, 12), mat);
    arrow.rotation.x = Math.PI / 2;
    arrow.position.set(0, figure ? 2.6 : 3.2, figure ? 0.4 : 0.6);
    // A larger see-through shell, so the marker is easy to click from afar.
    const hit = new THREE.Mesh(new THREE.CylinderGeometry(1.6, 1.6, 4.2, 10),
                               new THREE.MeshBasicMaterial({transparent: true, opacity: 0, depthWrite: false}));
    hit.position.y = 2.1;
    g.add(body, arrow, hit);
    g.userData.missionMarker = kind;
    g.visible = false;
    ctx.scene.add(g);
    return g;
  }
  const markers = {start: makeMarker('start', 0xffd23f, false), giver: makeMarker('giver', 0x3fd7ff, true)};
  // The point a marker stands for: {position, heading}, or null.
  const pointOf = (kind) => kind === 'start' ? st.start
    : (st.giver.mode === 'placed' && st.giver.position ? st.giver : null);
  const describe = (p) => `At ${p.position.map((v) => (+v).toFixed(1)).join(', ')}, facing ${(+p.heading || 0).toFixed(0)} degrees.`;

  function drawMarker(kind) {
    const p = pointOf(kind), m = markers[kind];
    m.visible = !!p && !$('missions').hidden;
    if (!m.visible) ctx.release(m);
    if (!p) return;
    m.position.copy(ctx.threePos(p.position));
    m.rotation.set(0, THREE.MathUtils.degToRad(+p.heading || 0), 0);
  }
  function drawStart() {
    drawMarker('start');
    const s = st.start;
    $('ms-start-at').textContent = s ? describe(s) : 'Not set: the player begins where the contract leaves them (the HQ).';
    if (s) $('ms-start-heading').value = +s.heading || 0;
  }
  function drawGiver() {
    const g = st.giver;
    $('ms-giver-mode').value = g.mode;
    $('ms-giver-who').hidden = g.mode === 'none';
    $('ms-giver-placed').hidden = g.mode !== 'placed';
    $('ms-giver-template').value = g.template || '';
    $('ms-giver-side').value = g.side || 'civ';
    $('ms-giver-marker').value = g.marker || 'gold';
    $('ms-giver-requires').hidden = g.mode === 'none' || g.mode === 'sequence';   // the sequence decides when
    // Other missions of this map, as "Available after" ticks.
    const others = st.list.filter((x) => !st.current || x.id !== st.current.id);
    $('ms-requires').innerHTML = others.map((x) => `<label class="ms-req"><input type="checkbox" data-req="${x.id}"
        ${(st.requires || []).includes(x.id) ? 'checked' : ''}> ${x.id}: ${esc(x.doc.title || '(untitled)')}</label>`).join('')
      || '<span class="hint">No other missions on this map yet.</span>';
    $('ms-giver-name').value = g.name || '';
    $('ms-giver-prompt').value = g.prompt || '';
    $('ms-giver-at').textContent = g.position ? describe(g) : 'Not placed yet: Set here puts them in the middle of the view.';
    if (g.position) $('ms-giver-heading').value = +g.heading || 0;
    drawMarker('giver');
  }

  // Grabbing a marker: the gizmo on it, and how to use it.
  function grab(kind) {
    if (!pointOf(kind)) return status(kind === 'start' ? 'Set the start first' : 'Place the giver first (Set here)', true);
    drawMarker(kind);
    ctx.grab(markers[kind]);
    status(`${kind === 'start' ? 'Player start' : 'Mission giver'} selected: drag the arrows to move it; `
           + 'press E and drag the ring to turn it; W to move again');
  }
  // Clicking a marker grabs it; dragging moves it over the ground, turning sets the heading.
  function pick(ray) {
    let best = null;
    for (const [kind, m] of Object.entries(markers)) {
      if (!m.visible) continue;
      const h = ray.intersectObject(m, true)[0];
      if (h && (!best || h.distance < best.distance)) {
        best = {distance: h.distance, apply: () => grab(kind)};
      }
    }
    return best;
  }
  function markerMoved(m) {
    const kind = m.userData.missionMarker, p = pointOf(kind);
    if (!p) return;
    const g = ctx.gamePos(m.position);
    const y = ctx.groundAt(g[0], g[2]);
    m.position.y = ctx.threePos([g[0], y, g[2]]).y;
    p.position = [g[0], y, g[2]].map((v) => Math.round(v * 100) / 100);
    const f = new THREE.Vector3(0, 0, 1).applyQuaternion(m.quaternion);   // forward: (sin h, cos h)
    let h = THREE.MathUtils.radToDeg(Math.atan2(f.x, f.z));
    if (h < 0) h += 360;
    p.heading = Math.round(h);
    m.rotation.set(0, THREE.MathUtils.degToRad(p.heading), 0);
    if (kind === 'start') { $('ms-start-at').textContent = describe(p); $('ms-start-heading').value = p.heading; }
    else { $('ms-giver-at').textContent = describe(p); $('ms-giver-heading').value = p.heading; }
    edited();
  }

  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  // ---------------------------------------------------------------- loading
  async function load(map) {
    st.map = map;
    const r = await fetch(`/api/missions/${map}`);
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || r.statusText);
    st.list = doc.missions;
    st.next = doc.next;
    st.shared = {squads: doc.squads || [], zones: doc.zones || [], paths: doc.paths || [], sets: doc.sets || {}};
    st.hq = doc.hq || {};
    if (st.current) st.current = st.list.find((m) => m.id === st.current.id) || null;
    fill();
  }

  function show(on) {
    $('missions').hidden = !on;
    $('missions-mode').classList.toggle('on', on);
    drawMarker('start');
    drawMarker('giver');
    if (on && st.map) load(st.map).catch((e) => status(e.message, true));
  }

  function fill() {
    $('ms-list').innerHTML = st.list.map((m) => `<option value="${m.id}">${m.id}: ${esc(m.doc.title || '(untitled)')}</option>`)
      .join('') + '<option value="">New mission...</option>';
    $('ms-list').value = st.current ? st.current.id : '';
    showCurrent();
  }

  // The mission file's "contact" as the panel's giver: false is no one; one
  // with a position is placed on the map; anything else is the HQ contact.
  // With "hq" the faction's own HQ bouncer offers it in its contract sequence
  // (and an HQ contact as well unless "contact" is false).
  function giverFrom(contact, hq) {
    if (hq && typeof hq === 'object') {
      const c = contact && typeof contact === 'object' ? contact : {};
      return {mode: 'sequence', after: +hq.after || 0, chapter: hq.chapter ?? null, contact: contact !== false,
              name: c.name || '', prompt: c.prompt || ''};
    }
    if (contact === false) return {mode: 'none'};
    const c = contact && typeof contact === 'object' ? contact : {};
    return {mode: c.position ? 'placed' : 'hq', template: c.template || '', name: c.name || '', prompt: c.prompt || '',
            side: c.side || 'civ', marker: c.marker || 'gold',
            position: c.position || null, heading: c.heading || 0};
  }

  // "In the faction's HQ contract sequence": which of the faction's retail
  // contracts on this map it follows (the server lists the points the HQ
  // reaches, hqsequence.plan), and whether an HQ contact offers it too.
  // Runs after drawGiver, which shows the contact's fields for any mode but none.
  function drawSequence() {
    const g = st.giver, on = g.mode === 'sequence';
    $('ms-giver-seq').hidden = !on;
    if (!on) return;
    $('ms-giver-who').hidden = !g.contact;
    $('ms-giver-contact').checked = !!g.contact;
    const faction = st.current ? st.current.faction : $('ms-faction').value;
    const points = ((st.hq || {})[faction] || {}).points || [];
    if (!points.length) {
      $('ms-giver-after').innerHTML = '<option value="">(none on this map)</option>';
      $('ms-giver-seq-hint').textContent = 'The HQ bouncers offer no contracts on this map.';
      return;
    }
    const p = points.find((x) => x.after === g.after && x.chapter === g.chapter)
      || points.find((x) => x.after === g.after && x.default) || points.find((x) => x.default) || points[0];
    g.after = p.after;
    g.chapter = p.chapter;
    $('ms-giver-after').innerHTML = points.map((x) => `<option value="${x.after}:${x.chapter}" ${x === p ? 'selected' : ''}>`
      + `${esc(x.label)}</option>`).join('');
    $('ms-giver-seq-hint').textContent = `The ${faction} HQ bouncer offers it at this point, before its next contract `
      + 'on this map, and waits there until it is done. If the chapter ends first, it is skipped.';
  }

  function showCurrent() {
    const m = st.current;
    const isNew = !m;
    $('ms-new').hidden = !isNew;
    $('ms-faction').disabled = !isNew;
    if (isNew) {
      $('ms-faction').value = $('ms-faction').value || 'allies';
      $('ms-number').textContent = `${st.map}_${$('ms-faction').value}${st.next[$('ms-faction').value] || 9}`;
      for (const id of ['ms-title', 'ms-description', 'ms-briefing']) $(id).value = '';
      $('ms-fee').value = 50000;
      $('ms-bonus').value = 0;
      st.objectives = [];
      st.fails = [];
      st.radio = [];
      st.orders = [];
      st.waves = [];
      st.start = null;
      st.giver = {mode: 'hq'};
      st.requires = [];
    } else {
      $('ms-faction').value = m.faction;
      $('ms-number').textContent = m.id;
      $('ms-title').value = m.doc.title || '';
      $('ms-description').value = m.doc.description || '';
      $('ms-briefing').value = (m.doc.mail || {}).body || '';
      $('ms-fee').value = (m.doc.ini || {}).ContractFee ?? 50000;
      $('ms-bonus').value = (m.doc.ini || {}).BonusFee ?? 0;
      st.objectives = (m.doc.objectives || []).map((o) => ({...o}));
      st.fails = (m.doc.fail || []).map((c) => ({...c}));
      st.radio = (m.doc.radio || []).map((r) => ({...r}));
      st.orders = (m.doc.orders || []).map((o) => ({...o}));
      st.waves = (m.doc.waves || []).map((w) => ({...w}));
      st.start = m.doc.start ? {...m.doc.start} : null;
      st.giver = giverFrom(m.doc.contact, m.doc.hq);
      st.requires = [...(m.doc.requires || [])];
    }
    $('ms-layer-tools').hidden = false;
    drawStart();
    drawGiver();
    drawSequence();
    $('ms-script-note').hidden = !(m && m.script);
    drawObjectives();
    drawFails();
    drawRadio();
    drawOrders();
    drawWaves();
    const id = m ? m.id : null;
    $('ms-start').textContent = id
      ? `Utility_WriteStringToScribbleMemory('SkipTo_Map','${st.map}') Utility_WriteStringToScribbleMemory('SkipTo_Faction','${m.faction}') `
        + `Utility_WriteNumberToScribbleMemory('SkipTo_MissionNumber',${m.number}) Utility_WriteStringToScribbleMemory('skipto_via_cheating','true') `
        + `Utility_EraseValueFromScribbleMemory('mission_accepted') Utility_ResetToBaseLayerOnly('${st.map}')`
      : '';
    $('ms-start-row').hidden = !id;
  }

  // ---------------------------------------------------------------- objectives
  const OBJ_NAMES = {destroy: 'Destroy squad / objects', goto: 'Go to zone', escort: 'Escort squad (they follow you to the zone)',
                     deliver: 'Deliver vehicle to zone', subdue: 'Capture alive (subdue)', hold: 'Hold zone',
                     use: 'Use object', verify: 'Verify target (kill and photograph, or capture)'};
  const BLIPS = {human: 'Person', scope: 'Scope', blip: 'Dot', clubs: 'Clubs', diamond: 'Diamonds', hearts: 'Hearts', spades: 'Spades'};
  function drawObjectives() {
    const m = st.current;
    const squads = m ? m.squads : (st.shared || {}).squads || [], zones = m ? m.zones : (st.shared || {}).zones || [];
    $('ms-objectives').innerHTML = st.objectives.map((o, i) => {
      const squadPick = `<select data-i="${i}" data-k="squad"><option value="">(squad)</option>${squads.map((s) =>
            `<option ${s === o.squad ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>`;
      const zonePick = `<select data-i="${i}" data-k="zone"><option value="">(${o.type === 'escort' ? 'to zone' : 'zone'})</option>${zones.map((z) =>
            `<option ${z === o.zone ? 'selected' : ''}>${esc(z)}</option>`).join('')}</select>`;
      // Objects picked on the map: destroy targets, or the vehicle to deliver.
      const targets = (o.type === 'destroy' || o.type === 'subdue' || o.type === 'use' || o.type === 'verify')
        ? `<span class="ms-targets">${(o.targets || []).map((t, j) => `<span class="ms-tag">${esc(t)}
            <button data-i="${i}" data-t="${j}" data-a="untarget" title="Remove">×</button></span>`).join('')}
            <button data-i="${i}" data-a="target" title="Select a named object on the map first">+ Add selected object</button></span>`
        : '';
      const vehicle = o.type === 'deliver'
        ? `<span class="ms-targets">${o.vehicle ? `<span class="ms-tag">${esc(o.vehicle)}</span>` : '(no vehicle)'}
            <button data-i="${i}" data-a="vehicle" title="Select the vehicle on the map first">Use selected vehicle</button></span>`
        : '';
      const seconds = o.type === 'hold'
        ? `<label>Seconds <input data-i="${i}" data-k="seconds" type="number" min="1" step="10" value="${+o.seconds || ''}"></label>` : '';
      const prompt = o.type === 'use'
        ? `<input data-i="${i}" data-k="prompt" type="text" value="${esc(o.prompt)}" placeholder="Prompt on the object, e.g. Plant charge">` : '';
      const verify = o.type === 'verify'
        ? `<label title="The marker on the targets">Marker <select data-i="${i}" data-k="blip">${Object.entries(BLIPS).map(([v, n]) =>
            `<option value="${v}" ${(o.blip || 'human') === v ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
           <label title="Paid on completion for each target captured alive">Alive $
            <input data-i="${i}" data-k="alive" type="number" min="0" step="1000" value="${+o.alive > 0 ? +o.alive : ''}" placeholder="none"></label>` : '';
      const pick = o.type === 'use' ? targets + prompt
        : o.type === 'verify' ? squadPick + targets + verify
        : (o.type === 'destroy' || o.type === 'subdue') ? squadPick + targets
        : o.type === 'escort' ? squadPick + zonePick
        : o.type === 'deliver' ? vehicle + zonePick : zonePick + seconds;
      return `<div class="ms-obj"><b>${i + 1}. ${OBJ_NAMES[o.type] || o.type}${+o.bonus > 0 ? ' (bonus)' : ''}</b>
        <input data-i="${i}" data-k="text" type="text" value="${esc(o.text)}" placeholder="What the player reads">
        ${pick}
        <label class="ms-bonus" title="A reward makes this a bonus objective: optional, active from the start alongside the main ones, paid on completion if done">Bonus $
          <input data-i="${i}" data-k="bonus" type="number" min="0" step="1000" value="${+o.bonus > 0 ? +o.bonus : ''}" placeholder="none"></label>
        ${+o.bonus > 0 ? revealPick(o, i, zones) : ''}
        <span class="buttons"><button data-i="${i}" data-a="show" title="Look at it on the map">Show</button>
        <button data-i="${i}" data-a="up">Up</button><button data-i="${i}" data-a="down">Down</button>
        <button data-i="${i}" data-a="del">Delete</button></span></div>`;
    }).join('') || '<p class="hint">No objectives yet.</p>';
  }

  // When a bonus objective is shown: at the start, when another objective
  // starts or is done ("starts:3", "done:3"), or on entering a zone ("zone:name").
  function revealPick(o, i, zones) {
    const r = o.reveal || 'start';
    const opts = [['start', 'At the start']];
    st.objectives.forEach((x, j) => {
      if (j === i) return;
      opts.push([`starts:${j + 1}`, `When objective ${j + 1} starts`], [`done:${j + 1}`, `When objective ${j + 1} is done`]);
    });
    opts.push(['zone', 'When the player enters a zone']);
    const isZone = r.startsWith('zone:');
    const sel = (v) => (isZone ? v === 'zone' : v === r) ? 'selected' : '';
    const zone = isZone ? r.slice(5) : '';
    return `<label class="ms-reveal">Shown <select data-i="${i}" data-k="reveal">${opts.map(([v, t]) =>
        `<option value="${v}" ${sel(v)}>${t}</option>`).join('')}</select></label>${isZone
      ? `<select data-i="${i}" data-k="revealZone"><option value="">(zone)</option>${zones.map((z) =>
          `<option ${z === zone ? 'selected' : ''}>${esc(z)}</option>`).join('')}</select>` : ''}`;
  }
  // After objectives move or go: point "starts:N" / "done:N" at the same
  // objectives (order[k] is the old index now at k); a reveal whose objective
  // is gone shows at the start.
  function renumber(order) {
    for (const o of st.objectives) {
      const m = /^(starts|done):(\d+)$/.exec(o.reveal || '');
      if (!m) continue;
      const k = order.indexOf(+m[2] - 1);
      o.reveal = k < 0 ? 'start' : `${m[1]}:${k + 1}`;
    }
    for (const r of [...st.radio, ...st.orders, ...st.waves]) {
      const m = /^(starts|done):(\d+)$/.exec(r.when || '');
      if (!m) continue;
      const k = order.indexOf(+m[2] - 1);
      r.when = k < 0 ? 'start' : `${m[1]}:${k + 1}`;
    }
    for (const c of st.fails) {
      const m = /^done:(\d+)$/.exec(c.until || '');
      if (!m) continue;
      const k = order.indexOf(+m[1] - 1);
      if (k < 0) delete c.until; else c.until = `done:${k + 1}`;
    }
  }

  function edited() { st.dirty = true; setDirty(true); }

  $('ms-objectives').addEventListener('input', (e) => {
    const i = e.target.dataset.i, k = e.target.dataset.k;
    if (i === undefined || !k) return;
    const o = st.objectives[+i];
    if (k === 'reveal') {
      const zones = st.current ? st.current.zones : (st.shared || {}).zones || [];
      o.reveal = e.target.value === 'zone' ? `zone:${zones[0] || ''}` : e.target.value;
      drawObjectives(); edited(); return;
    }
    if (k === 'revealZone') { o.reveal = `zone:${e.target.value}`; edited(); return; }
    const wasBonus = +o.bonus > 0;
    o[k] = (k === 'bonus' || k === 'seconds' || k === 'alive') ? Math.max(0, +e.target.value || 0) : e.target.value;
    if (k === 'bonus' && wasBonus !== +o.bonus > 0) {
      // became a bonus objective or stopped being one: its heading and "Shown"
      if (!(+o.bonus > 0)) delete o.reveal;
      drawObjectives();
      const box = $('ms-objectives').querySelector(`[data-i="${i}"][data-k="bonus"]`);
      if (box) { box.focus(); box.setSelectionRange?.(box.value.length, box.value.length); }
    }
    edited();
  });
  $('ms-objectives').addEventListener('click', (e) => {
    const i = +e.target.dataset.i, a = e.target.dataset.a;
    if (!a) return;
    const o = st.objectives;
    if (a === 'target' || a === 'vehicle') {
      const sel = ctx.selectedObject();
      if (!sel) return status('Select an object on the map first', true);
      if (!sel.name) return status(`That ${sel.model || 'object'} has no name the game can find: copy or place one to target it`, true);
      if (a === 'target') o[i].targets = [...new Set([...(o[i].targets || []), sel.name])];
      else o[i].vehicle = sel.name;
      drawObjectives(); edited();
      return;
    }
    if (a === 'untarget') {
      o[i].targets.splice(+e.target.dataset.t, 1);
      drawObjectives(); edited();
      return;
    }
    if (a === 'show') {
      const name = o[i].type === 'goto' ? o[i].zone : o[i].type === 'deliver' ? o[i].vehicle : o[i].squad || (o[i].targets || [])[0] || o[i].zone;
      if (!name) return status('Pick it first', true);
      ctx.show(name);
      return;
    }
    const order = o.map((x, k) => k);
    if (a === 'del') { o.splice(i, 1); order.splice(i, 1); }
    else if (a === 'up' && i > 0) { [o[i - 1], o[i]] = [o[i], o[i - 1]]; [order[i - 1], order[i]] = [order[i], order[i - 1]]; }
    else if (a === 'down' && i < o.length - 1) { [o[i + 1], o[i]] = [o[i], o[i + 1]]; [order[i + 1], order[i]] = [order[i], order[i + 1]]; }
    renumber(order);
    drawObjectives();
    drawFails();
    drawRadio();
    drawOrders();
    drawWaves();
    edited();
  });
  $('ms-add-destroy').onclick = () => { st.objectives.push({type: 'destroy', text: 'Destroy the squad', squad: ''}); drawObjectives(); drawFails(); drawRadio(); drawOrders(); drawWaves(); edited(); };
  $('ms-add-goto').onclick = () => { st.objectives.push({type: 'goto', text: 'Go to the marked area', zone: ''}); drawObjectives(); drawFails(); drawRadio(); drawOrders(); drawWaves(); edited(); };
  $('ms-add-verify').onclick = () => {
    st.objectives.push({type: 'verify', text: 'Verify the target', squad: '', targets: [], blip: 'human'});
    drawObjectives(); drawFails(); drawRadio(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-use').onclick = () => {
    st.objectives.push({type: 'use', text: 'Use the marked object', prompt: 'Use', targets: []});
    drawObjectives(); drawFails(); drawRadio(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-subdue').onclick = () => {
    st.objectives.push({type: 'subdue', text: 'Capture the target alive', squad: '', targets: []});
    drawObjectives(); drawFails(); drawRadio(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-hold').onclick = () => {
    st.objectives.push({type: 'hold', text: 'Hold the area', zone: '', seconds: 60});
    drawObjectives(); drawFails(); drawRadio(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-deliver').onclick = () => {
    st.objectives.push({type: 'deliver', text: 'Deliver the vehicle to the marked area', vehicle: '', zone: ''});
    drawObjectives(); drawFails(); drawRadio(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-escort').onclick = () => {
    st.objectives.push({type: 'escort', text: 'Escort them to the marked area', squad: '', zone: ''});
    drawObjectives(); drawFails(); drawRadio(); drawOrders(); drawWaves(); edited();
  };

  // ---------------------------------------------------------------- fail conditions
  // The mission fails if one happens: someone protected dies, time runs out,
  // or the player leaves a zone; each for the whole mission or until an
  // objective is done ("until": "done:N").
  const FAIL_NAMES = {dies: 'Protect squad (fail if one dies)', timer: 'Time limit', leave: 'Stay in zone (fail on leaving)'};
  function drawFails() {
    const m = st.current;
    const squads = m ? m.squads : (st.shared || {}).squads || [], zones = m ? m.zones : (st.shared || {}).zones || [];
    $('ms-fails').innerHTML = st.fails.map((c, k) => {
      const pick = c.type === 'dies'
        ? `<select data-k="squad" data-f="${k}"><option value="">(squad)</option>${squads.map((s) =>
            `<option ${s === c.squad ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>`
        : c.type === 'leave'
          ? `<select data-k="zone" data-f="${k}"><option value="">(zone)</option>${zones.map((z) =>
              `<option ${z === c.zone ? 'selected' : ''}>${esc(z)}</option>`).join('')}</select>`
          : `<label>Seconds <input data-k="seconds" data-f="${k}" type="number" min="1" step="10" value="${+c.seconds || ''}"></label>`;
      const until = `<label>Lasts <select data-k="until" data-f="${k}"><option value="">the whole mission</option>${st.objectives.map((o, j) =>
          `<option value="done:${j + 1}" ${c.until === `done:${j + 1}` ? 'selected' : ''}>until objective ${j + 1} is done</option>`).join('')}</select></label>`;
      return `<div class="ms-obj"><b>${FAIL_NAMES[c.type] || c.type}</b>
        <input data-k="text" data-f="${k}" type="text" value="${esc(c.text)}" placeholder="What the failure screen says">
        ${pick} ${until}
        <span class="buttons"><button data-f="${k}" data-a="del">Delete</button></span></div>`;
    }).join('') || '<p class="hint">None: the mission can only end in success.</p>';
  }
  $('ms-fails').addEventListener('input', (e) => {
    const k = e.target.dataset.f, key = e.target.dataset.k;
    if (k === undefined || !key) return;
    const c = st.fails[+k];
    if (key === 'seconds') c.seconds = Math.max(0, +e.target.value || 0);
    else if (e.target.value) c[key] = e.target.value;
    else delete c[key];
    edited();
  });
  $('ms-fails').addEventListener('click', (e) => {
    if (e.target.dataset.a !== 'del') return;
    st.fails.splice(+e.target.dataset.f, 1);
    drawFails(); edited();
  });
  // ---------------------------------------------------------------- radio lines
  // A line plays when the mission starts, an objective starts or is done, or
  // the player enters a zone: a voice ("<bank>.<cue>", with the text as its
  // subtitle) or, with no voice, the text as an on-screen message.
  function whenPick(value, attrs, zones) {
    const r = value || 'start';
    const opts = [['start', 'When the mission starts']];
    st.objectives.forEach((x, j) => opts.push([`starts:${j + 1}`, `When objective ${j + 1} starts`],
                                              [`done:${j + 1}`, `When objective ${j + 1} is done`]));
    opts.push(['zone', 'When the player enters a zone']);
    const isZone = r.startsWith('zone:');
    const zone = isZone ? r.slice(5) : '';
    return `<select ${attrs} data-k="when">${opts.map(([v, t]) =>
        `<option value="${v}" ${(isZone ? v === 'zone' : v === r) ? 'selected' : ''}>${t}</option>`).join('')}</select>${isZone
      ? `<select ${attrs} data-k="whenZone"><option value="">(zone)</option>${zones.map((z) =>
          `<option ${z === zone ? 'selected' : ''}>${esc(z)}</option>`).join('')}</select>` : ''}`;
  }
  function drawRadio() {
    const m = st.current;
    const zones = m ? m.zones : (st.shared || {}).zones || [];
    $('ms-radio').innerHTML = st.radio.map((r, k) => `<div class="ms-obj"><b>Line ${k + 1}</b>
        ${whenPick(r.when, `data-r="${k}"`, zones)}
        <input data-r="${k}" data-k="sound" type="text" value="${esc(r.sound)}" placeholder="Voice (optional): fio.S10START"
          title="A voice cue as bank.cue: the game's own, or one your mod adds as sounds/<bank>/new/<cue>.wav">
        <input data-r="${k}" data-k="text" type="text" value="${esc(r.text)}" placeholder="Subtitle, or the message with no voice">
        <span class="buttons"><button data-r="${k}" data-a="up">Up</button><button data-r="${k}" data-a="del">Delete</button></span></div>`)
      .join('') || '<p class="hint">No radio lines.</p>';
  }
  $('ms-radio').addEventListener('input', (e) => {
    const k = e.target.dataset.r, key = e.target.dataset.k;
    if (k === undefined || !key) return;
    const r = st.radio[+k];
    if (key === 'when') {
      const zones = st.current ? st.current.zones : (st.shared || {}).zones || [];
      r.when = e.target.value === 'zone' ? `zone:${zones[0] || ''}` : e.target.value;
      drawRadio(); edited(); return;
    }
    if (key === 'whenZone') r.when = `zone:${e.target.value}`;
    else r[key] = e.target.value.trim();
    edited();
  });
  $('ms-radio').addEventListener('click', (e) => {
    const k = +e.target.dataset.r, a = e.target.dataset.a;
    if (!a) return;
    if (a === 'del') st.radio.splice(k, 1);
    else if (a === 'up' && k > 0) [st.radio[k - 1], st.radio[k]] = [st.radio[k], st.radio[k - 1]];
    drawRadio(); edited();
  });
  // ---------------------------------------------------------------- squads that appear later
  // Only the mission's own squads (its layer is split at build time).
  function drawWaves() {
    const m = st.current;
    const own = m ? (m.squads || []).filter((s) => (m.own || []).includes(s)) : [];
    const zones = (m || st.shared || {}).zones || [];
    $('ms-waves').innerHTML = st.waves.map((w, k) => `<div class="ms-obj"><b>Squad ${k + 1}</b>
        <select data-w="${k}" data-k="squad"><option value="">(squad)</option>${own.map((s) =>
          `<option ${s === w.squad ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>
        appears ${whenPick(w.when, `data-w="${k}"`, zones)}
        <span class="buttons"><button data-w="${k}" data-a="del">Delete</button></span></div>`)
      .join('') || '<p class="hint">None: every squad is there from the start.</p>';
  }
  $('ms-waves').addEventListener('input', (e) => {
    const k = e.target.dataset.w, key = e.target.dataset.k;
    if (k === undefined || !key) return;
    const w = st.waves[+k];
    if (key === 'when') {
      const zones = (st.current || st.shared || {}).zones || [];
      w.when = e.target.value === 'zone' ? `zone:${zones[0] || ''}` : e.target.value;
    } else if (key === 'whenZone') w.when = `zone:${e.target.value}`;
    else w[key] = e.target.value;
    drawWaves(); edited();
  });
  $('ms-waves').addEventListener('click', (e) => {
    if (e.target.dataset.a !== 'del') return;
    st.waves.splice(+e.target.dataset.w, 1);
    drawWaves(); edited();
  });
  $('ms-add-wave').onclick = () => { st.waves.push({when: 'start', squad: ''}); drawWaves(); edited(); };

  // ---------------------------------------------------------------- squad orders
  // At a moment (as radio lines), a squad attacks the player, holds, moves to
  // one of its encounter's point sets, closes in on the player, or walks a path.
  const ORDER_NAMES = {attack: 'Attack the player', hold: 'Hold position', move: 'Move to point set',
                       close: 'Close in on the player', path: 'Walk a path'};
  function drawOrders() {
    const m = st.current;
    const pool = m || st.shared || {};
    const squads = pool.squads || [], zones = pool.zones || [], paths = pool.paths || [], sets = pool.sets || {};
    $('ms-orders').innerHTML = st.orders.map((o, k) => {
      const to = o.do === 'move'
        ? `<select data-o="${k}" data-k="to"><option value="">(point set)</option>${(sets[o.squad] || ['A', 'B', 'C']).map((x) =>
            `<option ${x === o.to ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>`
        : o.do === 'path'
          ? `<select data-o="${k}" data-k="to"><option value="">(path)</option>${paths.map((x) =>
              `<option ${x === o.to ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>` : '';
      return `<div class="ms-obj"><b>Order ${k + 1}</b>
        ${whenPick(o.when, `data-o="${k}"`, zones)}
        <select data-o="${k}" data-k="squad"><option value="">(squad)</option>${squads.map((s) =>
          `<option ${s === o.squad ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>
        <select data-o="${k}" data-k="do">${Object.entries(ORDER_NAMES).map(([v, t]) =>
          `<option value="${v}" ${v === o.do ? 'selected' : ''}>${t}</option>`).join('')}</select>
        ${to}
        <span class="buttons"><button data-o="${k}" data-a="del">Delete</button></span></div>`;
    }).join('') || '<p class="hint">No orders: squads stand where they are placed.</p>';
  }
  $('ms-orders').addEventListener('input', (e) => {
    const k = e.target.dataset.o, key = e.target.dataset.k;
    if (k === undefined || !key) return;
    const o = st.orders[+k];
    if (key === 'when') {
      const zones = (st.current || st.shared || {}).zones || [];
      o.when = e.target.value === 'zone' ? `zone:${zones[0] || ''}` : e.target.value;
    } else if (key === 'whenZone') o.when = `zone:${e.target.value}`;
    else {
      o[key] = e.target.value;
      if (key === 'do' || key === 'squad') delete o.to;     // a different list of places
    }
    drawOrders(); edited();
  });
  $('ms-orders').addEventListener('click', (e) => {
    if (e.target.dataset.a !== 'del') return;
    st.orders.splice(+e.target.dataset.o, 1);
    drawOrders(); edited();
  });
  $('ms-add-order').onclick = () => { st.orders.push({when: 'start', squad: '', do: 'attack'}); drawOrders(); edited(); };

  $('ms-add-radio').onclick = () => { st.radio.push({when: 'start', sound: '', text: ''}); drawRadio(); edited(); };

  $('ms-add-protect').onclick = () => { st.fails.push({type: 'dies', squad: ''}); drawFails(); edited(); };
  $('ms-add-timer').onclick = () => { st.fails.push({type: 'timer', seconds: 300}); drawFails(); edited(); };
  $('ms-add-leave').onclick = () => { st.fails.push({type: 'leave', zone: ''}); drawFails(); edited(); };

  // ---------------------------------------------------------------- the rest of the panel
  for (const id of ['ms-title', 'ms-description', 'ms-briefing', 'ms-fee', 'ms-bonus']) $(id).addEventListener('input', edited);
  // A new mission's faction picks its number only; what's typed stays.
  $('ms-faction').onchange = () => {
    $('ms-number').textContent = `${st.map}_${$('ms-faction').value}${st.next[$('ms-faction').value] || 9}`;
    edited();
  };
  $('ms-list').onchange = () => {
    $('ms-check-out').hidden = true;
    st.current = st.list.find((m) => m.id === $('ms-list').value) || null;
    showCurrent();
  };
  // The mission's own layer (made when the mission is saved, first in Save).
  const layerFile = () => `${st.current ? st.current.id : `${st.map}_${$('ms-faction').value}${st.next[$('ms-faction').value] || 9}`}.json`;
  $('ms-add-squad').onclick = () => ctx.newSquadIn(layerFile());
  $('ms-add-zone').onclick = () => ctx.drawZoneIn(layerFile());

  $('ms-start-set').onclick = () => {
    st.start = {position: ctx.viewCentre(), heading: +$('ms-start-heading').value || 0};
    drawStart(); edited();
    grab('start');
  };
  $('ms-start-grab').onclick = () => grab('start');
  $('ms-start-heading').addEventListener('input', () => {
    if (!st.start) return;
    st.start.heading = +$('ms-start-heading').value || 0;
    drawStart(); edited();
  });
  $('ms-start-clear').onclick = () => { st.start = null; drawStart(); edited(); };
  $('ms-start-show').onclick = () => st.start ? ctx.lookAt(st.start.position) : status('No start set', true);

  $('ms-giver-mode').onchange = () => {
    st.giver.mode = $('ms-giver-mode').value;
    drawGiver(); drawSequence(); edited();
  };
  for (const [id, k] of [['ms-giver-template', 'template'], ['ms-giver-name', 'name'], ['ms-giver-prompt', 'prompt']]) {
    $(id).addEventListener('input', () => { st.giver[k] = $(id).value.trim(); edited(); });
  }
  $('ms-giver-side').onchange = () => { st.giver.side = $('ms-giver-side').value; edited(); };
  $('ms-giver-marker').onchange = () => { st.giver.marker = $('ms-giver-marker').value; edited(); };
  $('ms-requires').addEventListener('change', (e) => {
    const id = e.target.dataset.req;
    if (!id) return;
    st.requires = (st.requires || []).filter((r) => r !== id);
    if (e.target.checked) st.requires.push(id);
    edited();
  });
  $('ms-giver-set').onclick = () => {
    st.giver.position = ctx.viewCentre();
    st.giver.heading = +$('ms-giver-heading').value || 0;
    drawGiver(); edited();
    grab('giver');
  };
  $('ms-giver-grab').onclick = () => grab('giver');
  $('ms-giver-heading').addEventListener('input', () => {
    if (!st.giver.position) return;
    st.giver.heading = +$('ms-giver-heading').value || 0;
    drawGiver(); edited();
  });
  $('ms-giver-show').onclick = () => st.giver.position ? ctx.lookAt(st.giver.position) : status('Not placed yet', true);

  // Check: the build's own checks on the saved mission.
  $('ms-check').onclick = async () => {
    const out = $('ms-check-out');
    out.hidden = false;
    if (!st.current) { out.innerHTML = '<p class="hint">Save the mission first: Check reads the saved file.</p>'; return; }
    out.innerHTML = '<p class="hint">Checking...</p>';
    try {
      const r = await fetch(`/api/missions/check/${st.current.id}`);
      const doc = await r.json();
      if (!r.ok) throw new Error(doc.error || r.statusText);
      const list = (items, cls) => items.map((x) => `<li class="${cls}">${esc(x)}</li>`).join('');
      out.innerHTML = (st.dirty ? '<p class="hint">You have unsaved changes: this checks the saved mission.</p>' : '')
        + (doc.problems.length ? `<p><b>${doc.problems.length} problem${doc.problems.length > 1 ? 's' : ''}</b>: the build won't write its script until they're fixed.</p>`
          : '<p><b>No problems</b>: the build writes its script.</p>')
        + `<ul>${list(doc.problems, 'ms-problem')}${list(doc.notes, 'ms-note')}</ul>`;
    } catch (e) {
      out.innerHTML = `<p class="hint">Couldn't check: ${esc(e.message)}</p>`;
    }
  };
  $('missions-mode').onclick = () => show($('missions').hidden);
  $('ms-close').onclick = () => show(false);

  // ---------------------------------------------------------------- saving
  function saveBody() {
    if (!st.dirty) return null;
    const isNew = !st.current;
    const faction = isNew ? $('ms-faction').value : st.current.faction;
    const number = isNew ? st.next[faction] : st.current.number;
    if (!$('ms-title').value.trim()) {
      // Nothing worth keeping yet: don't hold up saving everything else.
      if (isNew && !st.objectives.length && !$('ms-description').value.trim() && !$('ms-briefing').value.trim()) return null;
      throw new Error('Give the mission a title');
    }
    if (st.giver.mode === 'placed' && !st.giver.position) throw new Error('Place the mission giver (Set here), or pick another giver');
    return {map: st.map, mission: {
      faction, number, title: $('ms-title').value.trim(), description: $('ms-description').value.trim(),
      briefing: $('ms-briefing').value.trim(), fee: +$('ms-fee').value || 0, bonus: +$('ms-bonus').value || 0, objectives: st.objectives,
      start: st.start, giver: st.giver, requires: st.requires || [], fail: st.fails, radio: st.radio, orders: st.orders, waves: st.waves,
    }};
  }
  async function saved(done) {
    st.dirty = false;
    st.current = {id: done.id};
    await load(st.map);
  }

  return {load, saveBody, saved, show, pick, markerMoved, isOpen: () => !$('missions').hidden};
}
