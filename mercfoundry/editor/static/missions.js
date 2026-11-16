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
    $('ms-giver-stay').checked = !!g.stay;
    $('ms-giver-invincible').checked = !!g.invincible;
    $('ms-giver-lose').checked = !!g.lose_if_killed;
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
    st.arc = doc.arc || map;           // a new map's missions are in SW's arc
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
            stay: !!c.stay, invincible: !!c.invincible, lose_if_killed: !!c.lose_if_killed,
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
    if (undoTimer) flushUndo();
    const m = st.current;
    const isNew = !m;
    $('ms-new').hidden = !isNew;
    $('ms-file-tools').hidden = isNew;
    $('ms-faction').disabled = !isNew;
    if (isNew) {
      $('ms-faction').value = $('ms-faction').value || 'allies';
      $('ms-number').textContent = `${st.arc}_${$('ms-faction').value}${st.next[$('ms-faction').value] || 9}`;
      for (const id of ['ms-title', 'ms-description', 'ms-briefing']) $(id).value = '';
      $('ms-fee').value = 50000;
      $('ms-bonus').value = 0;
      $('ms-debug').checked = false;
      $('ms-keep-hostile').checked = false;
      st.testFrom = 0;
      for (const f of MOOD_FACTIONS) $(`ms-mood-${f}`).value = f === $('ms-faction').value ? 20 : '';
      st.objectives = [];
      st.fails = [];
      st.radio = [];
      st.music = [];
      st.weather = [];
      st.orders = [];
      st.waves = [];
      st.support = [];
      st.unlocks = [];
      $('ms-done-subject').value = '';
      $('ms-done-body').value = '';
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
      $('ms-debug').checked = !!m.doc.debug;
      $('ms-keep-hostile').checked = !!m.doc.keep_if_hostile;
      st.testFrom = +m.doc.test_from || 0;
      for (const f of MOOD_FACTIONS) $(`ms-mood-${f}`).value = (m.doc.mood || {})[f] || '';
      st.objectives = (m.doc.objectives || []).map((o) => ({...o}));
      st.fails = (m.doc.fail || []).map((c) => ({...c}));
      st.radio = (m.doc.radio || []).map((r) => ({...r}));
      st.music = (m.doc.music || []).map((x) => ({...x}));
      st.weather = (m.doc.weather || []).map((x) => ({...x}));
      st.orders = (m.doc.orders || []).map((o) => ({...o}));
      st.waves = (m.doc.waves || []).map((w) => ({...w}));
      st.support = (m.doc.support || []).map((s) => ({...s}));
      st.unlocks = [...(m.doc.unlocks || [])];
      $('ms-done-subject').value = (m.doc.done_mail || {}).subject || '';
      $('ms-done-body').value = (m.doc.done_mail || {}).body || '';
      st.start = m.doc.start ? {...m.doc.start} : null;
      st.giver = giverFrom(m.doc.contact, m.doc.hq);
      st.requires = [...(m.doc.requires || [])];
    }
    $('ms-layer-tools').hidden = false;
    drawSupport();
    drawUnlocks();
    drawStart();
    drawGiver();
    drawSequence();
    $('ms-script-note').hidden = !(m && m.script);
    drawObjectives();
    drawFails();
    drawRadio(); drawMusic(); drawWeather();
    drawOrders();
    drawWaves();
    drawOutline();
    const id = m ? m.id : null;
    // a mission played on a new map: its arc's map stays SkipTo_Map, and the
    // new map's own script starts it (foundry_play_map)
    const onNew = st.arc !== st.map;
    $('ms-start').textContent = id
      ? `Utility_WriteStringToScribbleMemory('SkipTo_Map','${st.arc}') Utility_WriteStringToScribbleMemory('SkipTo_Faction','${m.faction}') `
        + `Utility_WriteNumberToScribbleMemory('SkipTo_MissionNumber',${m.number}) Utility_WriteStringToScribbleMemory('skipto_via_cheating','true') `
        + (onNew ? `Utility_WriteStringToScribbleMemory('foundry_play_map','${st.map}') ` : '')
        + `Utility_EraseValueFromScribbleMemory('mission_accepted') Utility_ResetToBaseLayerOnly('${st.map}')`
      : '';
    $('ms-start-row').hidden = !id;
    undoBase = panelState();
  }

  // ---------------------------------------------------------------- objectives
  const MOOD_FACTIONS = ['allies', 'china', 'mafia', 'sk'];
  const OBJ_NAMES = {destroy: 'Destroy squad / objects', goto: 'Go to zone', escort: 'Escort squad (they follow you to the zone)',
                     deliver: 'Deliver vehicle to zone', subdue: 'Capture alive (subdue)', hold: 'Hold zone',
                     use: 'Use object', fast: 'Finish fast', verify: 'Verify target (kill and photograph, or capture)',
                     defend: 'Defend (keep them alive until the clock runs out)', collect: 'Collect items (pick them up)',
                     kill: 'Kill so many of a kind (anywhere)'};
  const BLIPS = {human: 'Person', scope: 'Scope', blip: 'Dot', clubs: 'Clubs', diamond: 'Diamonds', hearts: 'Hearts', spades: 'Spades'};
  // Folded cards: one line each. Which are open is kept by the objective
  // itself (so it survives moving them up and down).
  const openObjs = new WeakSet();
  function objSummary(o) {
    const what = [o.squad, ...(o.targets || []), ...(o.kinds || []), o.vehicle].filter(Boolean);
    const bits = [o.text ? `"${o.text}"` : '(no text)'];
    if (what.length) bits.push(what.length > 2 ? `${what[0]} +${what.length - 1}` : what.join(', '));
    else if (['destroy', 'subdue', 'use', 'verify', 'escort', 'defend', 'collect', 'kill'].includes(o.type)) bits.push('(nothing picked)');
    if (o.zone) bits.push(`to ${o.zone}`);
    else if (['goto', 'escort', 'deliver', 'hold'].includes(o.type)) bits.push('(no zone)');
    if (+o.seconds > 0) bits.push(clock(+o.seconds));
    if (+o.need > 0) bits.push(o.type === 'escort' || o.type === 'defend' ? `at least ${o.need}` : `${o.need} of them`);
    if (+o.bonus > 0) bits.push(money(o.bonus));
    if (o.together) bits.push('with the one before');
    return bits.join(' · ');
  }
  function pushObjective(o) {           // a new objective, open to fill in
    st.objectives.push(o);
    openObjs.add(o);
  }
  function drawObjectives() {
    const m = st.current;
    const squads = m ? m.squads : (st.shared || {}).squads || [], zones = m ? m.zones : (st.shared || {}).zones || [];
    $('ms-objectives').innerHTML = st.objectives.map((o, i) => {
      const squadPick = `<select data-i="${i}" data-k="squad"><option value="">(squad)</option>${squads.map((s) =>
            `<option ${s === o.squad ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>`;
      const zonePick = `<select data-i="${i}" data-k="zone"><option value="">(${o.type === 'escort' ? 'to zone' : 'zone'})</option>${zones.map((z) =>
            `<option ${z === o.zone ? 'selected' : ''}>${esc(z)}</option>`).join('')}</select>`;
      // Objects picked on the map: destroy targets, or the vehicle to deliver.
      const targets = ['destroy', 'subdue', 'use', 'verify', 'defend', 'collect'].includes(o.type)
        ? `<span class="ms-targets">${(o.targets || []).map((t, j) => `<span class="ms-tag">${esc(t)}
            <button data-i="${i}" data-t="${j}" data-a="untarget" title="Remove">×</button></span>`).join('')}
            <button data-i="${i}" data-a="target" title="Select a named object on the map first">+ Add selected object</button></span>`
        : '';
      const kinds = o.type === 'kill'
        ? `<span class="ms-targets">${(o.kinds || []).map((t, j) => `<span class="ms-tag">${esc(t)}
            <button data-i="${i}" data-t="${j}" data-a="unkind" title="Remove">×</button></span>`).join('')}
            <button data-i="${i}" data-a="kind" title="Select an object of the kind on the map first: every one of its model counts">+ Add selected object's kind</button></span>`
        : '';
      const vehicle = o.type === 'deliver'
        ? `<span class="ms-targets">${o.vehicle ? `<span class="ms-tag">${esc(o.vehicle)}</span>` : '(no vehicle)'}
            <button data-i="${i}" data-a="vehicle" title="Select the vehicle on the map first">Use selected vehicle</button></span>`
        : '';
      const seconds = (o.type === 'hold' || o.type === 'fast' || o.type === 'defend')
        ? `<label>Seconds <input data-i="${i}" data-k="seconds" type="number" min="1" step="10" value="${+o.seconds || ''}"></label>` : '';
      const prompt = o.type === 'use'
        ? `<input data-i="${i}" data-k="prompt" type="text" value="${esc(o.prompt)}" placeholder="Prompt on the object, e.g. Plant charge">` : '';
      const verify = o.type === 'verify'
        ? `<label title="The marker on the targets">Marker <select data-i="${i}" data-k="blip">${Object.entries(BLIPS).map(([v, n]) =>
            `<option value="${v}" ${(o.blip || 'human') === v ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
           <label title="Paid on completion for each target captured alive">Alive $
            <input data-i="${i}" data-k="alive" type="number" min="0" step="1000" value="${+o.alive > 0 ? +o.alive : ''}" placeholder="none"></label>` : '';
      const extract = o.type === 'subdue'
        ? `<label title="Done when they're flown out by the extraction chopper, not when knocked down"><input data-i="${i}" data-k="extract" type="checkbox" ${o.extract ? 'checked' : ''}> Extract them</label>` : '';
      const firstMain = st.objectives.findIndex((x) => !(+x.bonus > 0));
      const together = !(+o.bonus > 0) && firstMain >= 0 && i > firstMain
        ? `<label title="Starts with the main objective before it, in any order; the next one waits until both are done">`
          + `<input data-i="${i}" data-k="together" type="checkbox" ${o.together ? 'checked' : ''}> Together with the one before</label>` : '';
      const needText = {destroy: ['Done after this many (blank: all of them)', 'How many', 'all', ''],
                        escort: ['The mission fails once fewer than this many can still arrive (blank: one)', 'At least', '1', ' must arrive'],
                        defend: ['How many must still be alive when the clock runs out (blank: all of them)', 'At least', 'all', ' must live'],
                        collect: ['Done after this many are picked up (blank: all of them)', 'How many', 'all', ''],
                        kill: ['Done after this many of these kinds die, anywhere, however they die', 'How many', '10', '']}[o.type];
      const need = needText
        ? `<label title="${needText[0]}">${needText[1]} <input data-i="${i}" data-k="need" type="number" min="1" step="1" value="${+o.need > 0 ? +o.need : ''}" `
          + `placeholder="${needText[2]}">${needText[3]}</label>` : '';
      const pick = o.type === 'use' ? targets + prompt
        : o.type === 'verify' ? squadPick + targets + verify
        : o.type === 'subdue' ? squadPick + targets + extract
        : o.type === 'destroy' ? squadPick + targets
        : o.type === 'defend' ? squadPick + targets + seconds
        : o.type === 'collect' ? targets
        : o.type === 'kill' ? kinds
        : o.type === 'escort' ? squadPick + zonePick
        : o.type === 'deliver' ? vehicle + zonePick
        : o.type === 'fast' ? seconds : zonePick + seconds;
      const head = `<b class="ms-fold" data-i="${i}" data-a="fold" title="${openObjs.has(o) ? 'Fold' : 'Open'}">`
        + `${openObjs.has(o) ? '▾' : '▸'} ${i + 1}. ${OBJ_NAMES[o.type] || o.type}${+o.bonus > 0 ? ' (bonus)' : ''}</b>`;
      if (!openObjs.has(o)) {
        return `<div class="ms-obj ms-folded">${head} <span class="ms-summary" data-i="${i}" data-a="fold">${esc(objSummary(o))}</span></div>`;
      }
      return `<div class="ms-obj">${head}
        <input data-i="${i}" data-k="text" type="text" value="${esc(o.text)}" placeholder="What the player reads">
        ${pick} ${need} ${together}
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
    opts.push(['zone', 'When the player enters a zone'], ['dead', 'When a squad is wiped out'],
              ['near', 'When the player comes near a squad']);
    const isZone = r.startsWith('zone:'), isDead = r.startsWith('dead:') || r.startsWith('near:'), squadKind = r.slice(0, 4);
    const sel = (v) => (isZone ? v === 'zone' : isDead ? v === squadKind : v === r) ? 'selected' : '';
    const zone = isZone ? r.slice(5) : '';
    return `<label class="ms-reveal">Shown <select data-i="${i}" data-k="reveal">${opts.map(([v, t]) =>
        `<option value="${v}" ${sel(v)}>${t}</option>`).join('')}</select></label>${deadPick(r, `data-i="${i}"`, 'revealSquad')}${isZone
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
    for (const r of [...st.radio, ...st.music, ...st.weather, ...st.orders, ...st.waves]) {
      const m = /^(starts|done):(\d+)$/.exec(r.when || '');
      if (!m) continue;
      const k = order.indexOf(+m[2] - 1);
      r.when = k < 0 ? 'start' : `${m[1]}:${k + 1}`;
    }
    if (st.testFrom) {
      const k = order.indexOf(st.testFrom - 1);
      st.testFrom = k < 0 ? 0 : k + 1;
    }
    for (const c of st.fails) {
      const m = /^(starts|done):(\d+)$/.exec(c.from || '');
      if (!m) continue;
      const k = order.indexOf(+m[2] - 1);
      if (k < 0) delete c.from; else c.from = `${m[1]}:${k + 1}`;
    }
    for (const c of st.fails) {
      const m = /^done:(\d+)$/.exec(c.until || '');
      if (!m) continue;
      const k = order.indexOf(+m[1] - 1);
      if (k < 0) delete c.until; else c.until = `done:${k + 1}`;
    }
  }

  function edited() {
    st.dirty = true;
    setDirty(true);
    queueOutline();
    clearTimeout(undoTimer);
    undoTimer = setTimeout(flushUndo, 700);       // a burst of typing is one undo step
  }

  // ---------------------------------------------------------------- undo
  // The open mission's panel (its fields and its lists) before and after an
  // edit: one step on the editor's history, a moment after the last change.
  const UNDO_FIELDS = ['ms-title', 'ms-description', 'ms-briefing', 'ms-fee', 'ms-bonus', 'ms-done-subject', 'ms-done-body',
                       'ms-mood-allies', 'ms-mood-china', 'ms-mood-mafia', 'ms-mood-sk'];
  const UNDO_CHECKS = ['ms-debug', 'ms-keep-hostile'];
  let undoBase = null, undoTimer = null;
  const openId = () => (st.current ? st.current.id : null);
  function panelState() {
    return JSON.stringify({id: openId(), fields: UNDO_FIELDS.map((id) => $(id).value),
                           checks: UNDO_CHECKS.map((id) => $(id).checked), testFrom: st.testFrom || 0,
                           objectives: st.objectives, fails: st.fails, radio: st.radio, music: st.music,
                           weather: st.weather, orders: st.orders, waves: st.waves, support: st.support,
                           unlocks: st.unlocks, start: st.start, giver: st.giver, requires: st.requires});
  }
  function putPanelState(json) {
    const s = JSON.parse(json);
    if (s.id !== openId()) return status('That undo step is for another mission: open it first', true);
    UNDO_FIELDS.forEach((id, i) => { $(id).value = s.fields[i]; });
    UNDO_CHECKS.forEach((id, i) => { $(id).checked = s.checks[i]; });
    Object.assign(st, {testFrom: s.testFrom, objectives: s.objectives, fails: s.fails, radio: s.radio, music: s.music,
                       weather: s.weather, orders: s.orders, waves: s.waves, support: s.support, unlocks: s.unlocks,
                       start: s.start, giver: s.giver, requires: s.requires});
    drawSupport(); drawUnlocks(); drawStart(); drawGiver(); drawSequence();
    drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); drawOutline();
    st.dirty = true;
    setDirty(true);
    undoBase = json;
  }
  function flushUndo() {
    clearTimeout(undoTimer);
    undoTimer = null;
    if (undoBase === null || !st.objectives) return;
    const now = panelState();
    if (now === undoBase) return;
    const before = undoBase, after = now;
    undoBase = now;
    if (ctx.remember) {
      ctx.remember(`Edited mission ${openId() || '(new)'}`, () => putPanelState(before), () => putPanelState(after));
    }
  }

  // ---------------------------------------------------------------- tabs
  // The panel's parts, one at a time; the one last open is opened again.
  // Each tab's label counts what's in it.
  let msTab = 'overview';
  try { msTab = localStorage.getItem('foundry.missionsTab') || 'overview'; } catch (e) { /* no storage */ }
  function showTab(t) {
    if (!document.querySelector(`#missions .ms-tab[data-tab="${t}"]`)) t = 'overview';
    msTab = t;
    try { localStorage.setItem('foundry.missionsTab', t); } catch (e) { /* no storage */ }
    for (const d of document.querySelectorAll('#missions .ms-tab')) d.hidden = d.dataset.tab !== t;
    for (const b of document.querySelectorAll('#ms-tabs button')) b.classList.toggle('on', b.dataset.tab === t);
  }
  const TAB_NAMES = {overview: 'Overview', contract: 'Contract', giver: 'Giver & start', objectives: 'Objectives',
                     events: 'Events', fail: 'Fail', rewards: 'Rewards'};
  function tabCounts() {
    const mail = $('ms-done-subject').value.trim() || $('ms-done-body').value.trim() ? 1 : 0;
    const n = {objectives: st.objectives.length, events: st.waves.length + st.orders.length + st.radio.length + st.music.length + st.weather.length,
               fail: st.fails.length, rewards: st.support.length + st.unlocks.length + mail};
    for (const b of document.querySelectorAll('#ms-tabs button')) {
      const k = b.dataset.tab;
      b.innerHTML = esc(TAB_NAMES[k]) + (n[k] ? ` <span class="ms-count">${n[k]}</span>` : '');
    }
  }
  $('ms-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (b && b.dataset.tab) showTab(b.dataset.tab);
  });
  showTab(msTab);

  // ---------------------------------------------------------------- outline
  // The mission as a timeline, in words: what happens at the start, when each
  // objective starts and is done, on entering each zone, and what it pays.
  let outlineQueued = false;
  function queueOutline() {
    if (outlineQueued) return;
    outlineQueued = true;
    setTimeout(() => { outlineQueued = false; drawOutline(); }, 0);   // after the change that called edited()
  }
  const money = (n) => '$' + Math.round(+n || 0).toLocaleString('en-US');
  const clock = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
  function failWords(c) {
    const who = [c.squad, ...(c.targets || [])].filter(Boolean).join(', ') || '(nobody picked)';
    return {dies: `someone in ${who} dies`, timer: `time runs out (${clock(+c.seconds || 0)})`,
            leave: `the player leaves zone ${c.zone || '(none picked)'}`,
            alarm: `an alarm sounds (${(c.targets || []).join(', ') || 'none picked'})`,
            spotted: `${c.faction ? c.faction.toUpperCase() : '(no faction)'} spots the player trespassing`,
            hostile: `${who} turn hostile`,
            escapes: `${who} reach zone ${c.zone || '(none picked)'}`}[c.type] || c.type;
  }
  function drawOutline() {
    const box = $('ms-outline');
    if (!box) return;
    tabCounts();
    drawTestFrom();
    const objs = st.objectives;
    const objName = (i) => `${i + 1}. ${OBJ_NAMES[objs[i].type] || objs[i].type}: "${objs[i].text || ''}"`
      + (+objs[i].need > 0 ? (objs[i].type === 'escort' ? ` (at least ${objs[i].need} must arrive)` : ` (${objs[i].need} of them)`) : '')
      + (objs[i].type === 'fast' ? ` (complete within ${clock(+objs[i].seconds || 0)} of it showing)` : '');
    const at = {};
    const add = (when, line) => (at[when || 'start'] = at[when || 'start'] || []).push(line);
    for (const s of st.support) if (s.item) add('start', `Support: ${s.item.replace(/^template_support_/i, '')}${+s.count > 0 ? ` x${s.count}` : ''}`);
    objs.forEach((o, i) => { if (+o.bonus > 0) add(o.reveal || 'start', `Bonus objective shown: ${objName(i)} (${money(o.bonus)})`); });
    st.fails.forEach((c) => {
      add(c.from || 'start', `Fails if ${failWords(c)}${c.until ? ` (until objective ${c.until.slice(5)} is done)` : ''}`);
      if (c.until) add(c.until, `No longer fails if ${failWords(c)}`);
    });
    const later = (x) => (+x.delay > 0 ? ` (${x.delay} s later)` : '');
    st.waves.forEach((w) => add(w.when, `Squad ${w.squad || '(none picked)'} arrives${later(w)}`));
    st.orders.forEach((o) => add(o.when, `${o.squad || '(no squad)'}: ${(ORDER_NAMES[o.do] || o.do || '').toLowerCase()}${o.to ? ` (${o.to})` : ''}${later(o)}`));
    st.weather.forEach((x) => add(x.when, `Weather: ${x.preset === 'normal' ? 'back to normal' : x.preset}${later(x)}`));
    st.music.forEach((x) => add(x.when, `Music: ${x.play === 'normal' ? 'back to normal' : String(x.play || '').replace(/^music\./, '')}${later(x)}`));
    st.radio.forEach((r) => add(r.when, `Radio: ${r.text ? `"${r.text}"` : ''}${r.sound ? ` [${r.sound}]` : ''}${later(r)}`));
    const used = new Set();
    const block = (title, key, extra = []) => {
      used.add(key);
      const lines = [...extra, ...(at[key] || [])];
      return `<li><b>${esc(title)}</b>${lines.length ? `<ul>${lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>` : ''}</li>`;
    };
    const main = objs.map((o, i) => i).filter((i) => !(+objs[i].bonus > 0));
    const bonus = objs.map((o, i) => i).filter((i) => +objs[i].bonus > 0);
    let html = st.testFrom ? `<li class="ms-problem">TESTING: starts at objective ${st.testFrom}; the main objectives before it are skipped</li>` : '';
    html += block('Mission starts', 'start', [...(st.start ? ['The player starts at the set point'] : []),
      ...($('ms-keep-hostile').checked ? ['The contract goes on even if its faction turns hostile'] : [])]);
    // main objectives in groups: one, then any "together" after it
    const groups = [];
    for (const i of main) {
      if (objs[i].together && groups.length) groups[groups.length - 1].push(i); else groups.push([i]);
    }
    for (const g of groups) {
      for (const i of g) {
        html += block(`Objective ${objName(i)} starts${g.length > 1 && i !== g[0] ? ` (with objective ${g[0] + 1})` : ''}`, `starts:${i + 1}`);
      }
      for (const i of g) html += block(`Objective ${i + 1} is done`, `done:${i + 1}`);
      if (g.length > 1) html += `<li class="ms-note">The next objective waits for ${g.map((i) => i + 1).join(' and ')}, in any order</li>`;
    }
    for (const i of bonus) {
      if (at[`starts:${i + 1}`]) html += block(`Bonus objective ${i + 1} is shown`, `starts:${i + 1}`);
      if (at[`done:${i + 1}`]) html += block(`Bonus objective ${i + 1} is done`, `done:${i + 1}`);
    }
    for (const key of Object.keys(at)) {
      if (key.startsWith('zone:')) html += block(`The player enters zone ${key.slice(5) || '(none picked)'}`, key);
      if (key.startsWith('dead:')) html += block(`Squad ${key.slice(5) || '(none picked)'} is wiped out`, key);
      const near = /^near:([^:]*):(.*)$/.exec(key);
      if (near) html += block(`The player comes within ${near[1]} m of squad ${near[2] || '(none picked)'}`, key);
    }
    const flat = +$('ms-bonus').value || 0;
    const extras = objs.reduce((n, o) => n + (+o.bonus > 0 ? +o.bonus : 0), 0);
    const moods = MOOD_FACTIONS.map((f) => [f, +$(`ms-mood-${f}`).value || 0]).filter(([, n]) => n)
      .map(([f, n]) => `${f === 'sk' ? 'SK' : f[0].toUpperCase() + f.slice(1)} ${n > 0 ? '+' : ''}${n}`);
    const pay = [`Pays ${money($('ms-fee').value)}${flat ? ` + ${money(flat)} bonus` : ''}${extras ? ` + up to ${money(extras)} more for bonuses` : ''}`];
    objs.forEach((o, i) => { if (o.type === 'verify' && +o.alive > 0) pay.push(`+ ${money(o.alive)} for each target of objective ${i + 1} taken alive`); });
    if (moods.length) pay.push(`Mood: ${moods.join(', ')}`);
    for (const u of st.unlocks) if (u) pay.push(`Unlocks in the shop: ${u.replace(/^template_support_/i, '')}`);
    if ($('ms-done-subject').value.trim() || $('ms-done-body').value.trim()) pay.push(`Mail: "${$('ms-done-subject').value.trim()}"`);
    if (main.length) at.end = [...(at.end || []), ...pay];     // its radio lines play first, then it pays
    html += block(main.length ? 'Contract complete' : 'No main objective: the mission can\'t complete', 'end');
    const stray = Object.keys(at).filter((k) => !used.has(k));
    if (stray.length) html += `<li class="ms-problem">Nothing triggers ${stray.map(esc).join(', ')} (an objective that isn't there)</li>`;
    box.innerHTML = `<ul class="ms-outline">${html}</ul>`;
  }

  $('ms-objectives').addEventListener('input', (e) => {
    const i = e.target.dataset.i, k = e.target.dataset.k;
    if (i === undefined || !k) return;
    const o = st.objectives[+i];
    if (k === 'reveal') {
      const zones = st.current ? st.current.zones : (st.shared || {}).zones || [];
      o.reveal = momentOf(e.target.value);
      drawObjectives(); edited(); return;
    }
    if (k === 'revealZone') { o.reveal = `zone:${e.target.value}`; edited(); return; }
    if (k === 'revealSquadMetres') { o.reveal = nearMetres(o.reveal, e.target.value); edited(); return; }
    if (k === 'revealSquad') { o.reveal = squadMoment(o.reveal, e.target.value); edited(); return; }
    const wasBonus = +o.bonus > 0;
    if (k === 'extract' || k === 'together') {
      if (e.target.checked) o[k] = true; else delete o[k];
      queueOutline(); edited(); return;
    }
    if (k === 'need') {
      const n = Math.floor(+e.target.value || 0);
      if (n > 0) o.need = n; else delete o.need;
      edited(); return;
    }
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
    if (a === 'fold') {
      if (openObjs.has(o[i])) openObjs.delete(o[i]); else openObjs.add(o[i]);
      drawObjectives();
      return;
    }
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
    if (a === 'kind') {
      const sel = ctx.selectedObject();
      const model = sel && (sel.geometry || sel.model);
      if (!model) return status('Select an object of the kind on the map first', true);
      o[i].kinds = [...new Set([...(o[i].kinds || []), String(model).toLowerCase()])];
      drawObjectives(); edited();
      return;
    }
    if (a === 'unkind') {
      o[i].kinds.splice(+e.target.dataset.t, 1);
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
    drawRadio(); drawMusic(); drawWeather();
    drawOrders();
    drawWaves();
    edited();
  });
  $('ms-add-destroy').onclick = () => { pushObjective({type: 'destroy', text: 'Destroy the squad', squad: ''}); drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited(); };
  $('ms-add-goto').onclick = () => { pushObjective({type: 'goto', text: 'Go to the marked area', zone: ''}); drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited(); };
  $('ms-add-fast').onclick = () => {
    pushObjective({type: 'fast', text: 'Finish the contract within 5 minutes', seconds: 300, bonus: 20000});
    drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-verify').onclick = () => {
    pushObjective({type: 'verify', text: 'Verify the target', squad: '', targets: [], blip: 'human'});
    drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-use').onclick = () => {
    pushObjective({type: 'use', text: 'Use the marked object', prompt: 'Use', targets: []});
    drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-subdue').onclick = () => {
    pushObjective({type: 'subdue', text: 'Capture the target alive', squad: '', targets: []});
    drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-hold').onclick = () => {
    pushObjective({type: 'hold', text: 'Hold the area', zone: '', seconds: 60});
    drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-deliver').onclick = () => {
    pushObjective({type: 'deliver', text: 'Deliver the vehicle to the marked area', vehicle: '', zone: ''});
    drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-defend').onclick = () => {
    pushObjective({type: 'defend', text: 'Defend them until help arrives', squad: '', targets: [], seconds: 120});
    drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-kill').onclick = () => {
    pushObjective({type: 'kill', text: 'Kill 10 enemy soldiers', kinds: [], need: 10});
    drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-collect').onclick = () => {
    pushObjective({type: 'collect', text: 'Collect the marked items', targets: []});
    drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited();
  };
  $('ms-add-escort').onclick = () => {
    pushObjective({type: 'escort', text: 'Escort them to the marked area', squad: '', zone: ''});
    drawObjectives(); drawFails(); drawRadio(); drawMusic(); drawWeather(); drawOrders(); drawWaves(); edited();
  };

  // ---------------------------------------------------------------- fail conditions
  // The mission fails if one happens: someone protected dies, time runs out,
  // or the player leaves a zone; each for the whole mission or until an
  // objective is done ("until": "done:N").
  const FAIL_NAMES = {dies: 'Protect squad (fail if one dies)', timer: 'Time limit', leave: 'Stay in zone (fail on leaving)',
                      alarm: 'Alarm raised', spotted: 'Spotted trespassing', hostile: 'Someone turns hostile',
                      escapes: 'Target escapes (fail if they reach a zone)'};
  function drawFails() {
    const m = st.current;
    const squads = m ? m.squads : (st.shared || {}).squads || [], zones = m ? m.zones : (st.shared || {}).zones || [];
    $('ms-fails').innerHTML = st.fails.map((c, k) => {
      const squadPick = `<select data-k="squad" data-f="${k}"><option value="">(squad)</option>${squads.map((s) =>
            `<option ${s === c.squad ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>`;
      // alarm boxes, or people, picked on the map
      const targets = `<span class="ms-targets">${(c.targets || []).map((t, j) => `<span class="ms-tag">${esc(t)}
            <button data-f="${k}" data-t="${j}" data-a="untarget" title="Remove">×</button></span>`).join('')}
            <button data-f="${k}" data-a="target" title="Select a named object on the map first">+ Add selected ${c.type === 'alarm' ? 'alarm' : 'object'}</button></span>`;
      const pick = c.type === 'dies' ? squadPick
        : c.type === 'hostile' ? squadPick + targets
        : c.type === 'alarm' ? targets
        : c.type === 'spotted'
          ? `<label>By <select data-k="faction" data-f="${k}"><option value="">(faction)</option>${MOOD_FACTIONS.map((f) =>
              `<option value="${f}" ${f === c.faction ? 'selected' : ''}>${f === 'sk' ? 'SK' : f[0].toUpperCase() + f.slice(1)}</option>`).join('')}</select></label>`
        : c.type === 'leave' || c.type === 'escapes'
          ? (c.type === 'escapes' ? squadPick + targets : '') + `<select data-k="zone" data-f="${k}"><option value="">(${c.type === 'escapes' ? 'zone they run for' : 'zone'})</option>${zones.map((z) =>
              `<option ${z === c.zone ? 'selected' : ''}>${esc(z)}</option>`).join('')}</select>`
          : `<label>Seconds <input data-k="seconds" data-f="${k}" type="number" min="1" step="10" value="${+c.seconds || ''}"></label>`;
      const until = `<label>Lasts <select data-k="until" data-f="${k}"><option value="">the whole mission</option>${st.objectives.map((o, j) =>
          `<option value="done:${j + 1}" ${c.until === `done:${j + 1}` ? 'selected' : ''}>until objective ${j + 1} is done</option>`).join('')}</select></label>`;
      return `<div class="ms-obj"><b>${FAIL_NAMES[c.type] || c.type}</b>
        <input data-k="text" data-f="${k}" type="text" value="${esc(c.text)}" placeholder="What the failure screen says">
        ${pick} <label>Begins ${whenPick(c.from, `data-f="${k}"`, zones, 'from')}</label> ${until}
        <span class="buttons"><button data-f="${k}" data-a="del">Delete</button></span></div>`;
    }).join('') || '<p class="hint">None: the mission can only end in success.</p>';
  }
  $('ms-fails').addEventListener('input', (e) => {
    const k = e.target.dataset.f, key = e.target.dataset.k;
    if (k === undefined || !key) return;
    const c = st.fails[+k];
    if (key === 'seconds') c.seconds = Math.max(0, +e.target.value || 0);
    else if (key === 'from') {
      const zones = (st.current || st.shared || {}).zones || [];
      if (e.target.value === 'start') delete c.from;
      else c.from = momentOf(e.target.value);
      drawFails();
    } else if (key === 'fromZone') c.from = `zone:${e.target.value}`;
    else if (key === 'fromSquadMetres') c.from = nearMetres(c.from, e.target.value);
    else if (key === 'fromSquad') c.from = squadMoment(c.from, e.target.value);
    else if (e.target.value) c[key] = e.target.value;
    else delete c[key];
    edited();
  });
  $('ms-fails').addEventListener('click', (e) => {
    const a = e.target.dataset.a, k = +e.target.dataset.f;
    if (a === 'target') {
      const sel = ctx.selectedObject();
      if (!sel) return status('Select an object on the map first', true);
      if (!sel.name) return status(`That ${sel.model || 'object'} has no name the game can find: copy or place one to use it`, true);
      st.fails[k].targets = [...new Set([...(st.fails[k].targets || []), sel.name])];
      drawFails(); edited(); return;
    }
    if (a === 'untarget') {
      st.fails[k].targets.splice(+e.target.dataset.t, 1);
      drawFails(); edited(); return;
    }
    if (a !== 'del') return;
    st.fails.splice(+e.target.dataset.f, 1);
    drawFails(); edited();
  });
  // ---------------------------------------------------------------- radio lines
  // A line plays when the mission starts, an objective starts or is done, or
  // the player enters a zone: a voice ("<bank>.<cue>", with the text as its
  // subtitle) or, with no voice, the text as an on-screen message.
  // A moment's value from its picker: "zone" and "dead" take the first zone /
  // squad (their own picker beside it changes it).
  function momentOf(v) {
    const pool = st.current || st.shared || {};
    if (v === 'zone') return `zone:${(pool.zones || [])[0] || ''}`;
    if (v === 'dead') return `dead:${(pool.squads || [])[0] || ''}`;
    if (v === 'near') return `near:20:${(pool.squads || [])[0] || ''}`;
    return v;
  }
  // a squad picked for a squad's moment: wiped out, or the player near it (keeping its metres)
  function squadMoment(old, sq) {
    const m = /^near:([^:]*):/.exec(String(old || ''));
    return m ? `near:${m[1] || 20}:${sq}` : `dead:${sq}`;
  }
  function nearMetres(old, v) {
    const m = /^near:[^:]*:(.*)$/.exec(String(old || ''));
    return `near:${Math.max(1, Math.round(+v || 20))}:${m ? m[1] : ''}`;
  }
  // the squad picker for a "dead:" moment
  function deadPick(r, attrs, key) {
    r = String(r || '');
    const near = /^near:([^:]*):(.*)$/.exec(r);
    if (!r.startsWith('dead:') && !near) return '';
    const squads = (st.current || st.shared || {}).squads || [], sq = near ? near[2] : r.slice(5);
    return (near ? `<label title="How close the player comes to one of the squad">within
        <input ${attrs} data-k="${key}Metres" type="number" min="1" step="5" value="${+near[1] || 20}"> m of</label>` : '')
      + `<select ${attrs} data-k="${key}"><option value="">(squad)</option>${squads.map((s) =>
      `<option ${s === sq ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>`;
  }
  // "N seconds after" its moment (radio lines, squads that appear later, orders)
  function delayPick(x, attrs) {
    return `<label class="ms-delay" title="Seconds after its moment (blank: straight away)">after
      <input ${attrs} data-k="delay" type="number" min="0" step="5" value="${+x.delay > 0 ? +x.delay : ''}" placeholder="0"> s</label>`;
  }
  // a delay typed in: a number of seconds, or none
  function setDelay(x, v) {
    const n = Math.max(0, +v || 0);
    if (n > 0) x.delay = n; else delete x.delay;
  }
  function whenPick(value, attrs, zones, key = 'when', withEnd = false) {
    const r = value || 'start';
    const opts = [['start', 'When the mission starts']];
    if (withEnd) opts.push(['end', 'When the contract completes']);
    st.objectives.forEach((x, j) => opts.push([`starts:${j + 1}`, `When objective ${j + 1} starts`],
                                              [`done:${j + 1}`, `When objective ${j + 1} is done`]));
    opts.push(['zone', 'When the player enters a zone'], ['dead', 'When a squad is wiped out'],
              ['near', 'When the player comes near a squad']);
    const isZone = r.startsWith('zone:'), isDead = r.startsWith('dead:') || r.startsWith('near:'), squadKind = r.slice(0, 4);
    const zone = isZone ? r.slice(5) : '';
    return `<select ${attrs} data-k="${key}">${opts.map(([v, t]) =>
        `<option value="${v}" ${(isZone ? v === 'zone' : isDead ? v === squadKind : v === r) ? 'selected' : ''}>${t}</option>`).join('')}</select>`
      + deadPick(r, attrs, `${key}Squad`) + `${isZone
      ? `<select ${attrs} data-k="${key}Zone"><option value="">(zone)</option>${zones.map((z) =>
          `<option ${z === zone ? 'selected' : ''}>${esc(z)}</option>`).join('')}</select>` : ''}`;
  }
  // Voice lines: the game's own (searched by what's said, or cue), and the
  // mod's new ones; ▶ plays one, Use puts it on the radio line.
  let voices = null, voiceFor = null, voiceAudio = null;
  async function loadVoices() {
    if (!voices) {
      try { voices = await (await fetch('/api/voices')).json(); } catch (e) { voices = []; }
    }
    return voices;
  }
  function voiceText(cue) {
    const low = String(cue || '').toLowerCase();
    const v = (voices || []).find((x) => x.cue.toLowerCase() === low);
    return v ? v.text : '';
  }
  function drawVoices() {
    const q = $('ms-voice-q').value.trim().toLowerCase();
    const words = q.split(/\s+/).filter(Boolean);
    const hits = (voices || []).filter((v) => words.every((w) => v.text.toLowerCase().includes(w) || v.cue.toLowerCase().includes(w)));
    $('ms-voice-list').innerHTML = hits.slice(0, 60).map((v) => `<div class="ms-voice">
        <button data-play="${esc(v.cue)}" title="Listen">▶</button><button data-use="${esc(v.cue)}">Use</button>
        <b>${esc(v.cue)}</b>${v.mod ? ' (this mod)' : ''} ${esc(v.text)}</div>`).join('')
      + `<p class="hint">${hits.length > 60 ? `${hits.length} lines: showing 60, search for more words` : `${hits.length} line${hits.length === 1 ? '' : 's'}`}.`
      + ` With one of the game's voices, leave the line's text empty to keep its own subtitle: text there replaces that subtitle wherever the game plays the line.</p>`;
  }
  async function openVoices(k) {
    voiceFor = k;
    $('ms-voice-find').hidden = false;
    $('ms-voice-list').innerHTML = '<p class="hint">Loading the game\'s voice lines...</p>';
    // under the line it's for
    document.querySelectorAll('#ms-radio .ms-obj')[k].after($('ms-voice-find'));
    await loadVoices();
    drawVoices();
    $('ms-voice-q').focus();
  }
  $('ms-voice-q').addEventListener('input', drawVoices);
  $('ms-voice-list').addEventListener('click', (e) => {
    const play = e.target.dataset.play, use = e.target.dataset.use;
    if (play) {
      if (voiceAudio) voiceAudio.pause();
      voiceAudio = new Audio(`/api/voice/${encodeURIComponent(play)}.wav`);
      voiceAudio.play().catch((err) => status(`Couldn't play ${play}: ${err.message}`, true));
    } else if (use && voiceFor !== null && st.radio[voiceFor]) {
      st.radio[voiceFor].sound = use;
      $('ms-voice-find').hidden = true;
      $('ms-radio').after($('ms-voice-find'));
      drawRadio(); drawMusic(); drawWeather(); edited();
    }
  });

  // ---------------------------------------------------------------- music
  // A track at a moment (the game's own: card themes, faction action and
  // explore themes), or back to normal; ▶ plays it here.
  let tracks = null, trackAudio = null;
  async function drawMusic() {
    if (!tracks) {
      try { tracks = await (await fetch('/api/music')).json(); } catch (e) { tracks = []; }
    }
    const zones = (st.current || st.shared || {}).zones || [];
    $('ms-music').innerHTML = st.music.map((x, k) => `<div class="ms-obj"><b>Music ${k + 1}</b>
        ${whenPick(x.when, `data-m="${k}"`, zones, 'when', true)}${x.when === 'end' ? '' : delayPick(x, `data-m="${k}"`)}
        <select data-m="${k}" data-k="play"><option value="normal" ${x.play === 'normal' ? 'selected' : ''}>Back to normal (the faction's music)</option>
          ${tracks.map((t) => `<option value="${esc(t)}" ${t.toLowerCase() === String(x.play || '').toLowerCase() ? 'selected' : ''}>${esc(t.replace(/^music\./, ''))}</option>`).join('')}</select>
        <span class="buttons">${x.play && x.play !== 'normal' ? `<button data-m="${k}" data-a="play" title="Listen (a long track takes a moment)">▶</button>` : ''}
          <button data-m="${k}" data-a="del">Delete</button></span></div>`).join('')
      || '<p class="hint">No music changes: the faction\'s own plays.</p>';
  }
  $('ms-music').addEventListener('input', (e) => {
    const k = e.target.dataset.m, key = e.target.dataset.k;
    if (k === undefined || !key) return;
    const x = st.music[+k];
    if (key === 'delay') { setDelay(x, e.target.value); edited(); return; }
    if (key === 'when') x.when = momentOf(e.target.value);
    else if (key === 'whenZone') x.when = `zone:${e.target.value}`;
    else if (key === 'whenSquadMetres') x.when = nearMetres(x.when, e.target.value);
    else if (key === 'whenSquad') x.when = squadMoment(x.when, e.target.value);
    else x[key] = e.target.value;
    drawMusic(); edited();
  });
  $('ms-music').addEventListener('click', (e) => {
    const k = e.target.dataset.m, a = e.target.dataset.a;
    if (k === undefined || !a) return;
    if (a === 'del') { st.music.splice(+k, 1); drawMusic(); edited(); return; }
    if (a === 'play') {
      if (trackAudio) trackAudio.pause();
      status('Loading the track...');
      trackAudio = new Audio(`/api/voice/${encodeURIComponent(st.music[+k].play)}.wav`);
      trackAudio.play().then(() => status('')).catch((err) => status(`Couldn't play it: ${err.message}`, true));
    }
  });
  // ---------------------------------------------------------------- weather
  // One of the map's atmosphere presets at a moment (forced, as retail's
  // prison), or back to normal (the map's weather zones decide again).
  function drawWeather() {
    const zones = (st.current || st.shared || {}).zones || [];
    const presets = (ctx.skyPresets && ctx.skyPresets()) || [];
    $('ms-weather').innerHTML = st.weather.map((x, k) => `<div class="ms-obj"><b>Weather ${k + 1}</b>
        ${whenPick(x.when, `data-s="${k}"`, zones, 'when', true)}${x.when === 'end' ? '' : delayPick(x, `data-s="${k}"`)}
        <select data-s="${k}" data-k="preset"><option value="normal" ${x.preset === 'normal' ? 'selected' : ''}>Back to normal (the map's own)</option>
          ${presets.map((p) => `<option ${String(p).toLowerCase() === String(x.preset || '').toLowerCase() ? 'selected' : ''}>${esc(p)}</option>`).join('')}
          ${x.preset && x.preset !== 'normal' && !presets.some((p) => p.toLowerCase() === x.preset.toLowerCase()) ? `<option selected>${esc(x.preset)}</option>` : ''}</select>
        <span class="buttons"><button data-s="${k}" data-a="del">Delete</button></span></div>`).join('')
      || '<p class="hint">No weather changes: the map\'s own (its weather zones) decides.</p>';
  }
  $('ms-weather').addEventListener('input', (e) => {
    const k = e.target.dataset.s, key = e.target.dataset.k;
    if (k === undefined || !key) return;
    const x = st.weather[+k];
    if (key === 'delay') { setDelay(x, e.target.value); edited(); return; }
    if (key === 'when') x.when = momentOf(e.target.value);
    else if (key === 'whenZone') x.when = `zone:${e.target.value}`;
    else if (key === 'whenSquadMetres') x.when = nearMetres(x.when, e.target.value);
    else if (key === 'whenSquad') x.when = squadMoment(x.when, e.target.value);
    else x[key] = e.target.value;
    drawWeather(); edited();
  });
  $('ms-weather').addEventListener('click', (e) => {
    if (e.target.dataset.a !== 'del') return;
    st.weather.splice(+e.target.dataset.s, 1);
    drawWeather(); edited();
  });
  $('ms-add-weather').onclick = () => {
    const presets = (ctx.skyPresets && ctx.skyPresets()) || [];
    st.weather.push({when: 'start', preset: presets[0] || 'normal'});
    drawWeather(); edited();
  };
  $('ms-add-music').onclick = () => { st.music.push({when: 'start', play: 'music.Card_miniboss'}); drawMusic(); edited(); };

  function drawRadio() {
    if ($('ms-voice-find').parentElement === $('ms-radio')) $('ms-radio').after($('ms-voice-find'));
    const m = st.current;
    const zones = m ? m.zones : (st.shared || {}).zones || [];
    $('ms-radio').innerHTML = st.radio.map((r, k) => `<div class="ms-obj"><b>Line ${k + 1}</b>
        ${whenPick(r.when, `data-r="${k}"`, zones, 'when', true)}${r.when === 'end' ? '' : delayPick(r, `data-r="${k}"`)}
        <input data-r="${k}" data-k="sound" type="text" value="${esc(r.sound)}" placeholder="Voice (optional): fio.S10START"
          title="A voice cue as bank.cue: the game's own, or one your mod adds as sounds/<bank>/new/<cue>.wav">
        <button data-r="${k}" data-a="voice" title="Search the game's voice lines">Find voice</button>
        <input data-r="${k}" data-k="text" type="text" value="${esc(r.text)}"
          placeholder="${esc(r.sound && voiceText(r.sound) ? `Subtitle: ${voiceText(r.sound)}` : 'Subtitle, or the message with no voice')}">
        <span class="buttons"><button data-r="${k}" data-a="up">Up</button><button data-r="${k}" data-a="del">Delete</button></span></div>`)
      .join('') || '<p class="hint">No radio lines.</p>';
  }
  $('ms-radio').addEventListener('input', (e) => {
    const k = e.target.dataset.r, key = e.target.dataset.k;
    if (k === undefined || !key) return;
    const r = st.radio[+k];
    if (key === 'delay') { setDelay(r, e.target.value); edited(); return; }
    if (key === 'when') {
      const zones = st.current ? st.current.zones : (st.shared || {}).zones || [];
      r.when = momentOf(e.target.value);
      drawRadio(); drawMusic(); drawWeather(); edited(); return;
    }
    if (key === 'whenZone') r.when = `zone:${e.target.value}`;
    else if (key === 'whenSquadMetres') r.when = nearMetres(r.when, e.target.value);
    else if (key === 'whenSquad') r.when = squadMoment(r.when, e.target.value);
    else r[key] = e.target.value.trim();
    edited();
  });
  $('ms-radio').addEventListener('click', (e) => {
    if (e.target.dataset.a === 'voice') { openVoices(+e.target.dataset.r); return; }
    const k = +e.target.dataset.r, a = e.target.dataset.a;
    if (!a) return;
    if (a === 'del') st.radio.splice(k, 1);
    else if (a === 'up' && k > 0) [st.radio[k - 1], st.radio[k]] = [st.radio[k], st.radio[k - 1]];
    drawRadio(); drawMusic(); drawWeather(); edited();
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
        appears ${whenPick(w.when, `data-w="${k}"`, zones)}${delayPick(w, `data-w="${k}"`)}
        <span class="buttons"><button data-w="${k}" data-a="del">Delete</button></span></div>`)
      .join('') || '<p class="hint">None: every squad is there from the start.</p>';
  }
  $('ms-waves').addEventListener('input', (e) => {
    const k = e.target.dataset.w, key = e.target.dataset.k;
    if (k === undefined || !key) return;
    const w = st.waves[+k];
    if (key === 'delay') { setDelay(w, e.target.value); edited(); return; }
    if (key === 'when') {
      const zones = (st.current || st.shared || {}).zones || [];
      w.when = momentOf(e.target.value);
    } else if (key === 'whenZone') w.when = `zone:${e.target.value}`;
    else if (key === 'whenSquadMetres') w.when = nearMetres(w.when, e.target.value);
    else if (key === 'whenSquad') w.when = squadMoment(w.when, e.target.value);
    else w[key] = e.target.value;
    drawWaves(); edited();
  });
  $('ms-waves').addEventListener('click', (e) => {
    if (e.target.dataset.a !== 'del') return;
    st.waves.splice(+e.target.dataset.w, 1);
    drawWaves(); edited();
  });
  $('ms-add-wave').onclick = () => { st.waves.push({when: 'start', squad: ''}); drawWaves(); edited(); };

  // ---------------------------------------------------------------- support
  // Support items for the mission (airstrikes, vehicle drops, crates), as
  // retail's tSupport: added when it starts, the default back when it ends.
  let supportItems = null;
  async function drawSupport() {
    if (!supportItems) {
      supportItems = [];
      try {
        const all = await (await fetch('/api/templates')).json();
        supportItems = all.map((t) => t.name).filter((n) => /^template_support_/i.test(n)).sort((a, b) => a.localeCompare(b));
      } catch (e) { /* the list stays empty: names can still be typed */ }
      $('ms-support-items').innerHTML = supportItems.map((n) => `<option value="${esc(n)}">`).join('');
    }
    $('ms-support').innerHTML = st.support.map((s, k) => `<div class="ms-obj"><b>Item ${k + 1}</b>
        <input data-s="${k}" data-k="item" type="text" list="ms-support-items" value="${esc(s.item)}" placeholder="template_support_...">
        <label title="How many uses it gives (blank: retail's default)">Count <input data-s="${k}" data-k="count" type="number" min="0" step="1"
          value="${+s.count > 0 ? +s.count : ''}" placeholder="default"></label>
        <span class="buttons"><button data-s="${k}" data-a="del">Delete</button></span></div>`)
      .join('') || '<p class="hint">None: the player has their usual support.</p>';
  }
  $('ms-support').addEventListener('change', (e) => {
    const k = e.target.dataset.s, key = e.target.dataset.k;
    if (k === undefined || !key) return;
    st.support[+k][key] = key === 'count' ? Math.max(0, Math.floor(+e.target.value || 0)) : e.target.value.trim();
    edited();
  });
  $('ms-support').addEventListener('click', (e) => {
    if (e.target.dataset.a !== 'del') return;
    st.support.splice(+e.target.dataset.s, 1);
    drawSupport(); edited();
  });
  $('ms-add-support').onclick = () => { st.support.push({item: 'template_support_gunship', count: 1}); drawSupport(); edited(); };

  // ---------------------------------------------------------------- shop unlocks
  function drawUnlocks() {
    drawSupport();      // (fills the support items list the inputs share)
    $('ms-unlocks').innerHTML = st.unlocks.map((u, k) => `<div class="ms-obj">
        <input data-u="${k}" type="text" list="ms-support-items" value="${esc(u)}" placeholder="template_support_...">
        <span class="buttons"><button data-u="${k}" data-a="del">Delete</button></span></div>`).join('')
      || '<p class="hint">No shop unlocks.</p>';
  }
  $('ms-unlocks').addEventListener('change', (e) => {
    if (e.target.dataset.u === undefined || e.target.tagName !== 'INPUT') return;
    st.unlocks[+e.target.dataset.u] = e.target.value.trim();
    edited();
  });
  $('ms-unlocks').addEventListener('click', (e) => {
    if (e.target.dataset.a !== 'del') return;
    st.unlocks.splice(+e.target.dataset.u, 1);
    drawUnlocks(); edited();
  });
  $('ms-add-unlock').onclick = () => { st.unlocks.push('template_support_deliverH2'); drawUnlocks(); edited(); };
  for (const id of ['ms-done-subject', 'ms-done-body']) $(id).addEventListener('input', edited);

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
        ${whenPick(o.when, `data-o="${k}"`, zones)}${delayPick(o, `data-o="${k}"`)}
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
    if (key === 'delay') { setDelay(o, e.target.value); edited(); return; }
    if (key === 'when') {
      const zones = (st.current || st.shared || {}).zones || [];
      o.when = momentOf(e.target.value);
    } else if (key === 'whenZone') o.when = `zone:${e.target.value}`;
    else if (key === 'whenSquadMetres') o.when = nearMetres(o.when, e.target.value);
    else if (key === 'whenSquad') o.when = squadMoment(o.when, e.target.value);
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

  $('ms-add-radio').onclick = () => { st.radio.push({when: 'start', sound: '', text: ''}); drawRadio(); drawMusic(); drawWeather(); edited(); };

  $('ms-add-protect').onclick = () => { st.fails.push({type: 'dies', squad: ''}); drawFails(); edited(); };
  $('ms-add-timer').onclick = () => { st.fails.push({type: 'timer', seconds: 300}); drawFails(); edited(); };
  $('ms-add-leave').onclick = () => { st.fails.push({type: 'leave', zone: ''}); drawFails(); edited(); };
  $('ms-add-alarm').onclick = () => { st.fails.push({type: 'alarm', targets: []}); drawFails(); edited(); };
  $('ms-add-spotted').onclick = () => { st.fails.push({type: 'spotted', faction: ''}); drawFails(); edited(); };
  $('ms-add-hostile').onclick = () => { st.fails.push({type: 'hostile', squad: '', targets: []}); drawFails(); edited(); };
  $('ms-add-escapes').onclick = () => { st.fails.push({type: 'escapes', squad: '', targets: [], zone: ''}); drawFails(); edited(); };

  // ---------------------------------------------------------------- the rest of the panel
  for (const id of ['ms-title', 'ms-description', 'ms-briefing', 'ms-fee', 'ms-bonus', ...MOOD_FACTIONS.map((f) => `ms-mood-${f}`)]) {
    $(id).addEventListener('input', edited);
  }
  $('ms-outline-box').addEventListener('toggle', drawOutline);
  $('ms-debug').addEventListener('change', edited);
  $('ms-keep-hostile').addEventListener('change', edited);
  $('ms-test-from').addEventListener('change', () => { st.testFrom = +$('ms-test-from').value || 0; edited(); });
  // the main objectives it can start at (testing); redrawn with the outline
  function drawTestFrom() {
    const main = st.objectives.map((o, i) => [o, i]).filter(([o]) => !(+o.bonus > 0));
    if (st.testFrom && !main.some(([, i]) => i + 1 === st.testFrom)) st.testFrom = 0;   // gone, or now a bonus
    $('ms-test-from').innerHTML = '<option value="0">The first objective (normal)</option>'
      + main.slice(1).map(([o, i]) => `<option value="${i + 1}" ${st.testFrom === i + 1 ? 'selected' : ''}>`
        + `Objective ${i + 1}: ${esc(o.text || OBJ_NAMES[o.type] || o.type)}</option>`).join('');
    $('ms-test-from').classList.toggle('ms-warn', !!st.testFrom);
  }
  // A new mission's faction picks its number only; what's typed stays.
  $('ms-faction').onchange = () => {
    // a new mission's own-faction mood (20 by default) goes with the faction
    const own = $('ms-faction').value, set = MOOD_FACTIONS.filter((f) => $(`ms-mood-${f}`).value !== '');
    const from = set.length === 1 && set[0] !== own && $(`ms-mood-${set[0]}`).value === '20' ? set[0] : null;
    if (from) { $(`ms-mood-${own}`).value = $(`ms-mood-${from}`).value; $(`ms-mood-${from}`).value = ''; }
    $('ms-number').textContent = `${st.arc}_${$('ms-faction').value}${st.next[$('ms-faction').value] || 9}`;
    edited();
  };
  $('ms-list').onchange = () => {
    $('ms-check-out').hidden = true;
    st.current = st.list.find((m) => m.id === $('ms-list').value) || null;
    showCurrent();
  };
  // The mission's own layer (made when the mission is saved, first in Save).
  const layerFile = () => `${st.current ? st.current.id : `${st.arc}_${$('ms-faction').value}${st.next[$('ms-faction').value] || 9}`}.json`;
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
  for (const [id, k] of [['ms-giver-stay', 'stay'], ['ms-giver-invincible', 'invincible'], ['ms-giver-lose', 'lose_if_killed']]) {
    $(id).onchange = () => { st.giver[k] = $(id).checked; edited(); };
  }
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
  // Duplicate and delete: the server copies or moves the saved files.
  async function post(url, body) {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || r.statusText);
    return doc;
  }
  const makeTest = async (which) => {
    if (!confirm(`Make feature test${which > 1 ? ` ${which}` : ''} around the middle of the view? It adds a mission and its layer to the mod (about 150 m of flat-ish ground works best).`)) return;
    try {
      const doc = await post('/api/missions/testmission', {map: st.map, centre: ctx.viewCentre(), which});
      st.current = {id: doc.id};
      await load(st.map);
      status(`Made ${doc.id}: save is done; start the game through the launcher, then take the contract at the HQ or run its Lua line (Overview)`);
    } catch (e) { status(e.message, true); }
  };
  $('ms-testmission').onclick = () => makeTest(1);
  $('ms-testmission2').onclick = () => makeTest(2);
  $('ms-testmission3').onclick = () => makeTest(3);
  $('ms-duplicate').onclick = async () => {
    if (!st.current) return;
    if (st.dirty && !confirm('Duplicate copies the saved mission: your unsaved changes aren\'t in the copy. Go on?')) return;
    try {
      const doc = await post('/api/missions/duplicate', {id: st.current.id, faction: $('ms-dup-faction').value || undefined});
      st.current = {id: doc.id};
      st.dirty = false;   // (the editor's other unsaved edits stay)
      await load(st.map);
      status(`Copied to ${doc.id}${doc.notes.length ? ': ' + doc.notes.join('; ') : ''}`);
    } catch (e) { status(e.message, true); }
  };
  $('ms-delete').onclick = async () => {
    if (!st.current) return;
    const id = st.current.id;
    if (!confirm(`Delete ${id}? Its files move to the mod's .trash folder (move them back to undo).`)) return;
    try {
      const doc = await post('/api/missions/delete', {id});
      st.current = null;
      st.dirty = false;   // (the editor's other unsaved edits stay)
      await load(st.map);
      status(`Deleted ${id}: moved to ${doc.trash}`
        + (doc.required_by.length ? `. Still listed under "Available after" by ${doc.required_by.join(', ')}` : ''),
        doc.required_by.length > 0);
    } catch (e) { status(e.message, true); }
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
      briefing: $('ms-briefing').value.trim(), fee: +$('ms-fee').value || 0, bonus: +$('ms-bonus').value || 0, debug: $('ms-debug').checked, keep_if_hostile: $('ms-keep-hostile').checked, test_from: st.testFrom || 0,
      mood: Object.fromEntries(MOOD_FACTIONS.map((f) => [f, Math.max(-100, Math.min(100, Math.round(+$(`ms-mood-${f}`).value || 0)))])), objectives: st.objectives,
      start: st.start, giver: st.giver, requires: st.requires || [], fail: st.fails, radio: st.radio, music: st.music, weather: st.weather, orders: st.orders, waves: st.waves, support: st.support, unlocks: st.unlocks,
      done_mail: {subject: $('ms-done-subject').value.trim(), body: $('ms-done-body').value.trim()},
    }};
  }
  async function saved(done) {
    st.dirty = false;
    st.current = {id: done.id};
    await load(st.map);
  }

  return {load, saveBody, saved, show, pick, markerMoved, isOpen: () => !$('missions').hidden, flushUndo};
}
