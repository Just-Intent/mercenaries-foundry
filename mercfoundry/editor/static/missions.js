// Missions: contracts made in the editor. A mission is
// missions/<map>_<faction><n>.json (title, description, briefing mail, fee and
// objectives) plus its layer, encounters/<same name>.json, where its squads and
// zones go (the mission loads it). With objectives and no script of the mod's
// own, the build writes the mission's script: objectives one after another,
// then the contract completes. A contact at the faction's HQ offers it.
import * as THREE from 'three';

export function createMissions(ctx) {
  const {$, status, setDirty} = ctx;
  const st = {map: null, list: [], next: {}, current: null, dirty: false, start: null};
  // The player's start: a yellow arrow on the map, pointing the way they face.
  const marker = new THREE.Group();
  {
    const mat = new THREE.MeshLambertMaterial({color: 0xffd23f});
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.25, 0.25, 3, 8), mat);
    post.position.y = 1.5;
    const arrow = new THREE.Mesh(new THREE.ConeGeometry(0.8, 2, 12), mat);
    arrow.rotation.x = Math.PI / 2;            // points along +z, the game's forward at heading 0
    arrow.position.set(0, 3.2, 0.6);
    marker.add(post, arrow);
    marker.userData.start = true;
    marker.visible = false;
    ctx.scene.add(marker);
  }
  function drawStart() {
    const s = st.start;
    marker.visible = !!s && !$('missions').hidden;
    if (!marker.visible) ctx.release(marker);
    $('ms-start-at').textContent = s
      ? `At ${s.position.map((v) => v.toFixed(1)).join(', ')}, facing ${(+s.heading || 0).toFixed(0)} degrees.`
      : 'Not set: the player begins where the contract leaves them (the HQ).';
    if (!s) return;
    marker.position.copy(ctx.threePos(s.position));
    marker.rotation.y = THREE.MathUtils.degToRad(+s.heading || 0);
    $('ms-start-heading').value = (+s.heading || 0);
  }

  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  async function load(map) {
    st.map = map;
    const r = await fetch(`/api/missions/${map}`);
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || r.statusText);
    st.list = doc.missions;
    st.next = doc.next;
    st.shared = {squads: doc.squads || [], zones: doc.zones || []};
    if (st.current) st.current = st.list.find((m) => m.id === st.current.id) || null;
    fill();
  }

  function show(on) {
    $('missions').hidden = !on;
    $('missions-mode').classList.toggle('on', on);
    marker.visible = on && !!st.start;
    if (!marker.visible) ctx.release(marker);
    if (on && st.map) load(st.map).catch((e) => status(e.message, true));
  }

  function fill() {
    $('ms-list').innerHTML = st.list.map((m) => `<option value="${m.id}">${m.id}: ${esc(m.doc.title || '(untitled)')}</option>`)
      .join('') + '<option value="">New mission...</option>';
    $('ms-list').value = st.current ? st.current.id : '';
    showCurrent();
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
      st.start = null;
    } else {
      $('ms-faction').value = m.faction;
      $('ms-number').textContent = m.id;
      $('ms-title').value = m.doc.title || '';
      $('ms-description').value = m.doc.description || '';
      $('ms-briefing').value = (m.doc.mail || {}).body || '';
      $('ms-fee').value = (m.doc.ini || {}).ContractFee ?? 50000;
      $('ms-bonus').value = (m.doc.ini || {}).BonusFee ?? 0;
      st.objectives = (m.doc.objectives || []).map((o) => ({...o}));
      st.start = m.doc.start ? {...m.doc.start} : null;
    }
    $('ms-layer-tools').hidden = false;
    drawStart();
    $('ms-script-note').hidden = !(m && m.script);
    drawObjectives();
    const id = m ? m.id : null;
    $('ms-start').textContent = id
      ? `Utility_WriteStringToScribbleMemory('SkipTo_Map','${st.map}') Utility_WriteStringToScribbleMemory('SkipTo_Faction','${m.faction}') `
        + `Utility_WriteNumberToScribbleMemory('SkipTo_MissionNumber',${m.number}) Utility_WriteStringToScribbleMemory('skipto_via_cheating','true') `
        + `Utility_EraseValueFromScribbleMemory('mission_accepted') Utility_ResetToBaseLayerOnly('${st.map}')`
      : '';
    $('ms-start-row').hidden = !id;
  }

  function drawObjectives() {
    const m = st.current;
    const squads = m ? m.squads : (st.shared || {}).squads || [], zones = m ? m.zones : (st.shared || {}).zones || [];
    $('ms-objectives').innerHTML = st.objectives.map((o, i) => {
      const pick = o.type === 'destroy'
        ? `<select data-i="${i}" data-k="squad"><option value="">(squad)</option>${squads.map((s) =>
            `<option ${s === o.squad ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>`
        : `<select data-i="${i}" data-k="zone"><option value="">(zone)</option>${zones.map((z) =>
            `<option ${z === o.zone ? 'selected' : ''}>${esc(z)}</option>`).join('')}</select>`;
      return `<div class="ms-obj"><b>${i + 1}. ${o.type === 'destroy' ? 'Destroy squad' : 'Go to zone'}</b>
        <input data-i="${i}" data-k="text" type="text" value="${esc(o.text)}" placeholder="What the player reads">
        ${pick}
        <span class="buttons"><button data-i="${i}" data-a="show" title="Look at it on the map">Show</button>
        <button data-i="${i}" data-a="up">Up</button><button data-i="${i}" data-a="down">Down</button>
        <button data-i="${i}" data-a="del">Delete</button></span></div>`;
    }).join('') || '<p class="hint">No objectives yet.</p>';
  }

  function edited() { st.dirty = true; setDirty(true); }

  $('ms-objectives').addEventListener('input', (e) => {
    const i = e.target.dataset.i, k = e.target.dataset.k;
    if (i === undefined || !k) return;
    st.objectives[+i][k] = e.target.value;
    edited();
  });
  $('ms-objectives').addEventListener('click', (e) => {
    const i = +e.target.dataset.i, a = e.target.dataset.a;
    if (!a) return;
    const o = st.objectives;
    if (a === 'show') {
      const name = o[i].type === 'destroy' ? o[i].squad : o[i].zone;
      if (!name) return status('Pick it first', true);
      ctx.show(name);
      return;
    }
    if (a === 'del') o.splice(i, 1);
    else if (a === 'up' && i > 0) [o[i - 1], o[i]] = [o[i], o[i - 1]];
    else if (a === 'down' && i < o.length - 1) [o[i + 1], o[i]] = [o[i], o[i + 1]];
    drawObjectives();
    edited();
  });
  $('ms-add-destroy').onclick = () => { st.objectives.push({type: 'destroy', text: 'Destroy the squad', squad: ''}); drawObjectives(); edited(); };
  $('ms-add-goto').onclick = () => { st.objectives.push({type: 'goto', text: 'Go to the marked area', zone: ''}); drawObjectives(); edited(); };
  for (const id of ['ms-title', 'ms-description', 'ms-briefing', 'ms-fee', 'ms-bonus']) $(id).addEventListener('input', edited);
  // A new mission's faction picks its number only; what's typed stays.
  $('ms-faction').onchange = () => {
    $('ms-number').textContent = `${st.map}_${$('ms-faction').value}${st.next[$('ms-faction').value] || 9}`;
    edited();
  };
  $('ms-list').onchange = () => {
    st.current = st.list.find((m) => m.id === $('ms-list').value) || null;
    showCurrent();
  };
  // The mission's own layer (made when the mission is saved, first in Save).
  const layerFile = () => `${st.current ? st.current.id : `${st.map}_${$('ms-faction').value}${st.next[$('ms-faction').value] || 9}`}.json`;
  $('ms-add-squad').onclick = () => ctx.newSquadIn(layerFile());
  $('ms-add-zone').onclick = () => ctx.drawZoneIn(layerFile());
  $('ms-start-set').onclick = () => {
    const at = ctx.viewCentre();
    st.start = {position: at, heading: +$('ms-start-heading').value || 0};
    drawStart(); edited();
  };
  $('ms-start-heading').addEventListener('input', () => {
    if (!st.start) return;
    st.start.heading = +$('ms-start-heading').value || 0;
    drawStart(); edited();
  });
  $('ms-start-clear').onclick = () => { st.start = null; drawStart(); edited(); };
  $('ms-start-show').onclick = () => st.start ? ctx.lookAt(st.start.position) : status('No start set', true);
  $('missions-mode').onclick = () => show($('missions').hidden);
  $('ms-close').onclick = () => show(false);

  // Clicking the marker grabs it; dragging moves it over the ground, turning sets the heading.
  function pick(ray) {
    if (!marker.visible) return null;
    const h = ray.intersectObject(marker, true)[0];
    return h ? {distance: h.distance, apply: () => { ctx.grab(marker); status('Player start: drag to move it; E to turn it, W to move again'); }} : null;
  }
  function startMoved() {
    if (!st.start) return;
    const g = ctx.gamePos(marker.position);
    const y = ctx.groundAt(g[0], g[2]);
    marker.position.y = ctx.threePos([g[0], y, g[2]]).y;
    st.start.position = [g[0], y, g[2]].map((v) => Math.round(v * 100) / 100);
    const f = new THREE.Vector3(0, 0, 1).applyQuaternion(marker.quaternion);   // forward: (sin h, cos h)
    let h = THREE.MathUtils.radToDeg(Math.atan2(f.x, f.z));
    if (h < 0) h += 360;
    st.start.heading = Math.round(h);
    marker.rotation.set(0, THREE.MathUtils.degToRad(st.start.heading), 0);
    $('ms-start-at').textContent = `At ${st.start.position.map((v) => v.toFixed(1)).join(', ')}, facing ${st.start.heading} degrees.`;
    $('ms-start-heading').value = st.start.heading;
    edited();
  }

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
    return {map: st.map, mission: {
      faction, number, title: $('ms-title').value.trim(), description: $('ms-description').value.trim(),
      briefing: $('ms-briefing').value.trim(), fee: +$('ms-fee').value || 0, bonus: +$('ms-bonus').value || 0, objectives: st.objectives,
      start: st.start,
    }};
  }
  async function saved(done) {
    st.dirty = false;
    st.current = {id: done.id};
    await load(st.map);
  }

  return {load, saveBody, saved, show, pick, startMoved, isOpen: () => !$('missions').hidden};
}
