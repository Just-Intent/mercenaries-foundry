// Squads and spawn points in the Foundry editor.
//
// An encounter has named spawn point sets ("points": {"A": [[x, y, z], ...]})
// and squads, each using some of the sets and holding members: copies of a
// template ("from") placed at a position and heading ("yaw"). The game's own
// encounters are shown read-only; the mod's (in its encounters/*.json layers)
// can be edited and created.
import * as THREE from 'three';

const MEMBER_GEO = new THREE.CapsuleGeometry(0.4, 1.0, 4, 8).translate(0, 0.9, 0);
const NOSE_GEO = new THREE.BoxGeometry(0.25, 0.25, 0.5).translate(0, 1.45, -0.45);   // faces the heading
const POINT_GEO = new THREE.ConeGeometry(0.7, 1.6, 10).rotateX(Math.PI).translate(0, 0.8, 0);
const COLOURS = {'game-member': 0x8fb8ff, 'mod-member': 0xff5ad7, 'game-point': 0xd8c060,
                 'mod-point': 0xff9a3c, selected: 0xffffff};

export function createEncounters(ctx) {
  const {scene, gizmo, heightAt, threePos, gamePos, status, $} = ctx;
  const group = new THREE.Group();
  scene.add(group);
  const mats = {};
  const mat = (key) => (mats[key] ||= new THREE.MeshLambertMaterial({color: COLOURS[key]}));
  const st = {map: null, game: [], mod: [], templates: [], selected: null, dirty: new Set(), place: null, show: true};

  // ---------------------------------------------------------------- data access
  // A selection: {source, file, enc, squad?, member?, set?, index?}.
  function all() {
    const out = [];
    for (const enc of st.game) out.push({source: 'game', file: null, layer: enc.layer, enc});
    for (const f of st.mod) for (const enc of f.encounters) out.push({source: 'mod', file: f.file, layer: f.layer, enc});
    return out;
  }
  const same = (a, b) => a && b && a.enc === b.enc && a.member === b.member && a.set === b.set && a.index === b.index;

  // ---------------------------------------------------------------- drawing
  function rebuild() {
    group.clear();
    if (!st.show) return;
    for (const {source, file, layer, enc} of all()) {
      for (const [set, pts] of Object.entries(enc.points || {})) {
        pts.forEach((p, index) => {
          const sel = {source, file, layer, enc, set, index};
          const m = new THREE.Mesh(POINT_GEO, mat(same(sel, st.selected) ? 'selected' : `${source}-point`));
          m.position.copy(threePos(p));
          m.userData.pick = sel;
          group.add(m);
        });
      }
      for (const squad of enc.squads || []) {
        for (const member of squad.members || []) {
          const sel = {source, file, layer, enc, squad, member};
          const key = same(sel, st.selected) ? 'selected' : `${source}-member`;
          const g = new THREE.Group();
          g.add(new THREE.Mesh(MEMBER_GEO, mat(key)), new THREE.Mesh(NOSE_GEO, mat(key)));
          g.position.copy(threePos(member.position));
          g.rotation.y = THREE.MathUtils.degToRad(-(member.yaw || 0));
          for (const c of g.children) c.userData.pick = sel;
          g.userData.pick = sel;
          group.add(g);
          if (same(sel, st.selected)) sel.node = g;
        }
      }
    }
    if (st.selected && !st.selected.member) {
      st.selected.node = group.children.find((c) => same(c.userData.pick, st.selected));
    }
    if (st.selected && st.selected.source === 'mod' && st.selected.node) {
      gizmo.setMode('translate');
      gizmo.showX = gizmo.showY = gizmo.showZ = true;
      gizmo.attach(st.selected.node);
    }
  }

  // ---------------------------------------------------------------- loading
  async function load(map) {
    select(null);
    const [r, t] = await Promise.all([fetch(`/api/encounters/${map}`), st.templates.length ? null : fetch('/api/templates')]);
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || r.statusText);
    if (t) {
      st.templates = await t.json();
      $('templates').innerHTML = st.templates.map((x) =>
        `<option value="${x.name}">${x.type}${x.faction ? ', ' + x.faction : ''} (${x.model})</option>`).join('');
    }
    st.map = map;
    st.game = doc.game;
    st.mod = doc.mod;
    st.dirty.clear();
    rebuild();
    return st.game.length + st.mod.reduce((n, f) => n + f.encounters.length, 0);
  }

  // ---------------------------------------------------------------- selection
  function select(sel) {
    if (gizmo.object && gizmo.object.userData.pick) gizmo.detach();
    st.selected = sel;
    rebuild();
    showPanel();
  }

  function pick(ray) {
    if (st.place) return null;
    const hit = ray.intersectObjects(group.children, true)[0];
    if (!hit) return null;
    const sel = hit.object.userData.pick;
    return {distance: hit.distance, apply: () => { ctx.deselectOthers(); select({...sel}); }};
  }

  function moved(node) {
    const sel = st.selected;
    if (!sel || sel.source !== 'mod') return;
    const p = gamePos(node.position);
    if (sel.member) sel.member.position = p;
    else sel.enc.points[sel.set][sel.index] = p;
    edited(sel.file);
    showPanel();
  }

  function edited(file) {
    st.dirty.add(file);
    ctx.setDirty(true);
  }

  // ---------------------------------------------------------------- panel
  function showPanel() {
    const sel = st.selected;
    $('enc').hidden = !sel;
    if (!sel) return;
    const mod = sel.source === 'mod';
    if (sel.member) {
      $('enc-title').textContent = sel.member.name || '(unnamed member)';
      $('enc-what').textContent = `${mod ? 'Mod' : 'Game'} squad member`;
      $('enc-from').textContent = sel.member.from || sel.member.model || '';
    } else {
      $('enc-title').textContent = `Spawn point ${sel.set}${sel.index}`;
      $('enc-what').textContent = `${mod ? 'Mod' : 'Game'} spawn point (set ${sel.set})`;
      $('enc-from').textContent = '';
    }
    $('enc-name').textContent = sel.enc.name;
    $('enc-squad').textContent = sel.squad ? `${sel.squad.name} (spawn points: ${(sel.squad.points || []).join(', ') || 'none'})` : '';
    $('enc-layer').textContent = sel.layer;
    const p = sel.member ? sel.member.position : sel.enc.points[sel.set][sel.index];
    [$('ex').value, $('ey').value, $('ez').value] = p.map((v) => (+v).toFixed(2));
    $('eh-row').hidden = !sel.member;
    if (sel.member) $('eh').value = (sel.member.yaw || 0).toFixed(1);
    for (const id of ['ex', 'ey', 'ez', 'eh']) $(id).disabled = !mod;
    for (const id of ['enc-add-member', 'enc-add-point', 'enc-delete', 'enc-delete-squad', 'enc-delete-enc']) $(id).hidden = !mod;
    $('enc-add-member').hidden = !mod || !sel.member;
    $('enc-delete-squad').hidden = !mod || !sel.squad;
    $('enc-note').textContent = mod
      ? `Saved in encounters/${sel.file}. A script loads the layer with Utility_ReadLevelFile('${sel.layer}') and sends squads with Squad_Command.`
      : 'A game encounter (read only here). Its squads are usually spawned by mission scripts at these points.';
  }

  function fromPanel() {
    const sel = st.selected;
    if (!sel || sel.source !== 'mod') return;
    const p = [$('ex').value, $('ey').value, $('ez').value].map((v) => (v.trim() === '' ? NaN : +v));
    if (p.some((v) => !Number.isFinite(v))) return showPanel();
    if (sel.member) {
      sel.member.position = p;
      const h = +$('eh').value;
      if (Number.isFinite(h)) sel.member.yaw = Math.round(h * 10) / 10;
    } else {
      sel.enc.points[sel.set][sel.index] = p;
    }
    edited(sel.file);
    select(sel);
  }

  // ---------------------------------------------------------------- editing
  function names() {
    const out = new Set(ctx.objectNames());
    for (const {enc} of all()) for (const sq of enc.squads || []) for (const m of sq.members || []) out.add((m.name || '').toLowerCase());
    return out;
  }
  function freshName(base) {
    const taken = names();
    let k = 1;
    while (taken.has(`${base}${k}`.toLowerCase())) k++;
    return `${base}${k}`;
  }

  function addMember() {
    const sel = st.selected;
    if (!sel || sel.source !== 'mod' || !sel.member) return;
    const m = {...sel.member, position: [sel.member.position[0] + 2, 0, sel.member.position[2]],
               name: freshName(`${sel.enc.name}_${sel.squad.name}_`)};
    m.position[1] = Math.round(heightAt(m.position[0], m.position[2]) * 1000) / 1000;
    sel.squad.members.push(m);
    edited(sel.file);
    select({...sel, member: m});
  }

  function deleteSelected() {
    const sel = st.selected;
    if (!sel || sel.source !== 'mod') return;
    if (sel.member) {
      sel.squad.members = sel.squad.members.filter((m) => m !== sel.member);
    } else {
      const pts = sel.enc.points[sel.set];
      pts.splice(sel.index, 1);
      if (!pts.length) {
        delete sel.enc.points[sel.set];
        for (const sq of sel.enc.squads || []) sq.points = (sq.points || []).filter((s) => s !== sel.set);
      }
    }
    edited(sel.file);
    select(null);
  }

  function deleteSquad() {
    const sel = st.selected;
    if (!sel || sel.source !== 'mod' || !sel.squad) return;
    if (!confirm(`Delete squad ${sel.squad.name} and its ${sel.squad.members.length} members?`)) return;
    sel.enc.squads = sel.enc.squads.filter((s) => s !== sel.squad);
    edited(sel.file);
    select(null);
  }

  function deleteEncounter() {
    const sel = st.selected;
    if (!sel || sel.source !== 'mod') return;
    if (!confirm(`Delete encounter ${sel.enc.name}, its squads and spawn points?`)) return;
    const f = st.mod.find((x) => x.file === sel.file);
    f.encounters = f.encounters.filter((e) => e !== sel.enc);
    edited(sel.file);
    select(null);
  }

  // Click-to-place modes: a new spawn point, or a new squad.
  function startAddPoint() {
    const sel = st.selected;
    if (!sel || sel.source !== 'mod') return;
    const set = sel.set || (sel.squad && (sel.squad.points || [])[0]) || Object.keys(sel.enc.points || {})[0] || 'A';
    st.place = {kind: 'point', sel, set};
    status(`Click the ground to add a spawn point to set ${set}; Esc cancels.`);
  }

  function startNewSquad() {
    ctx.deselectOthers();
    select(null);
    const fresh = `${ctx.slug(ctx.modName())}_${st.map}.json`;
    const files = [...new Set([...st.mod.map((f) => f.file), ...ctx.layoutFiles(), fresh])];
    $('sq-file').innerHTML = files.map((f) => `<option>${f}</option>`).join('');
    $('sq-file').value = files[0];
    fillEncounters();
    $('sq-name').value = 'Squad1';
    $('sq-count').value = 3;
    if (!$('sq-template').value) $('sq-template').value = 'template_A6_NK_Soldier';
    $('squad-form').hidden = false;
  }

  function fillEncounters() {
    const f = st.mod.find((x) => x.file === $('sq-file').value);
    const encs = f ? f.encounters.map((e) => e.name) : [];
    $('sq-enc').innerHTML = encs.map((n) => `<option>${n}</option>`).join('') + '<option value="">New encounter...</option>';
    $('sq-enc-new-row').hidden = encs.length > 0;
    $('sq-enc-new').value = `${ctx.slug(ctx.modName())}_enc1`.replace(/^(\d)/, 'e$1');
  }

  function placeSquad() {
    const template = $('sq-template').value.trim();
    if (!st.templates.some((t) => t.name.toLowerCase() === template.toLowerCase()) && !ctx.objectNames().has(template.toLowerCase())) {
      return status(`No template or named object called ${template}`, true);
    }
    const count = Math.max(1, Math.min(12, +$('sq-count').value || 1));
    const encName = $('sq-enc').value || $('sq-enc-new').value.trim();
    const squadName = $('sq-name').value.trim();
    if (!/^[A-Za-z0-9_]+$/.test(encName) || !/^[A-Za-z0-9_]+$/.test(squadName)) {
      return status('Encounter and squad names use letters, digits and _ only', true);
    }
    st.place = {kind: 'squad', file: $('sq-file').value, encName, squadName, template, count};
    $('squad-form').hidden = true;
    status(`Click the ground to place ${count} member${count > 1 ? 's' : ''} of ${squadName}; Esc cancels.`);
  }

  function placeClick(ray) {
    if (!st.place) return false;
    const hit = ray.intersectObject(ctx.terrain())[0];
    if (!hit) return true;
    const at = gamePos(hit.point);
    const pl = st.place;
    st.place = null;
    if (pl.kind === 'point') {
      const enc = pl.sel.enc;
      enc.points = enc.points || {};
      (enc.points[pl.set] ||= []).push(at);
      if (pl.sel.squad && !(pl.sel.squad.points || []).includes(pl.set)) (pl.sel.squad.points ||= []).push(pl.set);
      edited(pl.sel.file);
      select({...pl.sel, member: undefined, squad: pl.sel.squad, set: pl.set, index: enc.points[pl.set].length - 1});
      status('Spawn point added');
      return true;
    }
    // A new squad: members in a row across the view, facing the camera.
    let f = st.mod.find((x) => x.file === pl.file);
    if (!f) { f = {file: pl.file, layer: pl.file.replace(/\.json$/, ''), encounters: []}; st.mod.push(f); }
    let enc = f.encounters.find((e) => e.name === pl.encName);
    if (!enc) {
      enc = {name: pl.encName, points: {A: [[at[0], at[1], at[2] - 10]]}, squads: []};
      enc.points.A[0][1] = Math.round(heightAt(at[0], at[2] - 10) * 1000) / 1000;
      f.encounters.push(enc);
    }
    if (enc.squads.some((s) => s.name === pl.squadName)) return status(`${pl.encName} already has a squad ${pl.squadName}`, true);
    const cam = ctx.camera.position;
    const toCam = Math.atan2(cam.x - hit.point.x, cam.z - hit.point.z);       // three.js angle to the camera
    const yaw = Math.round(THREE.MathUtils.radToDeg(Math.PI - toCam) * 10) / 10;
    const side = new THREE.Vector3(Math.cos(toCam), 0, -Math.sin(toCam));
    const squad = {name: pl.squadName, points: Object.keys(enc.points).slice(0, 1), members: []};
    enc.squads.push(squad);
    for (let i = 0; i < pl.count; i++) {
      const p = hit.point.clone().addScaledVector(side, (i - (pl.count - 1) / 2) * 2.5);
      const g = gamePos(p);
      g[1] = Math.round(heightAt(g[0], g[2]) * 1000) / 1000;
      // Each name is fresh against the members already added.
      squad.members.push({from: pl.template, name: freshName(`${pl.encName}_${pl.squadName}_`), position: g, yaw});
    }
    edited(pl.file);
    select({source: 'mod', file: f.file, layer: f.layer, enc, squad, member: squad.members[0]});
    status(`${pl.squadName} placed with ${pl.count} members`);
    return true;
  }

  function cancelPlace() {
    st.place = null;
    $('squad-form').hidden = true;
  }

  // ---------------------------------------------------------------- saving
  function saveBody() {
    if (!st.dirty.size) return null;
    const files = {};
    for (const file of st.dirty) {
      const f = st.mod.find((x) => x.file === file);
      files[file] = f ? f.encounters : [];
    }
    return {map: st.map, files};
  }

  function saved() { st.dirty.clear(); }

  function find(q) {
    q = q.toLowerCase();
    for (const s of all()) {
      if (s.enc.name.toLowerCase().includes(q)) {
        const sq = (s.enc.squads || []).find((x) => (x.members || []).length);
        if (sq) return {...s, squad: sq, member: sq.members[0], at: sq.members[0].position};
        const set = Object.keys(s.enc.points || {})[0];
        if (set) return {...s, set, index: 0, at: s.enc.points[set][0]};
      }
      for (const sq of s.enc.squads || []) {
        for (const m of sq.members || []) {
          if ((m.name || '').toLowerCase().includes(q)) return {...s, squad: sq, member: m, at: m.position};
        }
      }
    }
    return null;
  }

  // ---------------------------------------------------------------- wiring
  $('new-squad').onclick = startNewSquad;
  $('sq-file').addEventListener('change', fillEncounters);
  $('sq-enc').addEventListener('change', () => { $('sq-enc-new-row').hidden = $('sq-enc').value !== ''; });
  $('sq-place').onclick = placeSquad;
  $('sq-cancel').onclick = cancelPlace;
  $('enc-add-member').onclick = addMember;
  $('enc-add-point').onclick = startAddPoint;
  $('enc-delete').onclick = deleteSelected;
  $('enc-delete-squad').onclick = deleteSquad;
  $('enc-delete-enc').onclick = deleteEncounter;
  for (const id of ['ex', 'ey', 'ez', 'eh']) $(id).addEventListener('change', fromPanel);
  $('show-squads').addEventListener('change', (e) => { st.show = e.target.checked; if (!st.show) select(null); rebuild(); });

  return {
    load, pick, select, moved, placeClick, cancelPlace, saveBody, saved, find, deleteSelected,
    isPlacing: () => !!st.place,
    files: () => st.mod.map((f) => f.file),
    hasSelection: () => !!st.selected,
  };
}
