// Animations: a character's animation table (which clip plays for each
// handle the game asks for, with its priority and flags), edited as the
// mod's animtables/<table>.changes.json, and a 3D preview of any clip on a
// model that uses the table.
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';

export function createAnimations(ctx) {
  const {$, status, setDirty} = ctx;
  const st = {tables: null, name: null, data: null, docs: {}, dirty: new Set(), set: null, filter: '', clips: null};
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const loader = new GLTFLoader();

  // ------------------------------------------------------------ the preview
  const pv = {renderer: null, scene: null, camera: null, controls: null, mixer: null, model: null, modelName: null,
              clock: new THREE.Clock(), playing: null, raf: 0};
  function startView() {
    if (pv.renderer) return;
    const canvas = $('an-view');
    pv.renderer = new THREE.WebGLRenderer({canvas, antialias: true});
    pv.renderer.setPixelRatio(window.devicePixelRatio);
    pv.scene = new THREE.Scene();
    pv.scene.background = new THREE.Color(0x2a2f36);
    pv.scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 2.2));
    const sun = new THREE.DirectionalLight(0xffffff, 1.5);
    sun.position.set(3, 6, 4);
    pv.scene.add(sun);
    const grid = new THREE.GridHelper(6, 12, 0x555c66, 0x3a4048);
    pv.scene.add(grid);
    pv.camera = new THREE.PerspectiveCamera(40, 1, 0.05, 100);
    pv.camera.position.set(2.6, 1.6, 2.6);
    pv.controls = new OrbitControls(pv.camera, canvas);
    pv.controls.target.set(0, 0.9, 0);
    pv.controls.update();
  }
  function frame() {
    pv.raf = 0;
    if ($('animations').hidden || !pv.renderer) return;
    const canvas = $('an-view');
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.floor(w * window.devicePixelRatio)) {
      pv.renderer.setSize(w, h, false);
      pv.camera.aspect = w / h;
      pv.camera.updateProjectionMatrix();
    }
    if (pv.mixer) pv.mixer.update(pv.clock.getDelta());
    pv.renderer.render(pv.scene, pv.camera);
    pv.raf = requestAnimationFrame(frame);
  }
  function run() { if (!pv.raf) pv.raf = requestAnimationFrame(frame); }
  async function showModel(name) {
    startView();
    if (pv.modelName === name) return;
    if (pv.model) { pv.scene.remove(pv.model); pv.model = null; pv.mixer = null; }
    pv.modelName = name;
    if (!name) return;
    const gltf = await loader.loadAsync(`/api/model/${encodeURIComponent(name)}.glb`);
    if (pv.modelName !== name) return;
    pv.model = gltf.scene;
    pv.scene.add(pv.model);
    pv.mixer = new THREE.AnimationMixer(pv.model);
    run();
  }
  async function play(clip) {
    const model = $('an-model').value;
    if (!model) return status('This table has no character model to show it on', true);
    if (!clip) return;
    try {
      await showModel(model);
      const gltf = await loader.loadAsync(`/api/clip/${encodeURIComponent(model)}/${encodeURIComponent(clip)}.glb`);
      const anim = gltf.animations[0];
      if (!anim || !pv.mixer) return status(`${clip}: no animation for ${model}'s joints`, true);
      pv.mixer.stopAllAction();
      pv.mixer.clipAction(anim).reset().play();
      pv.playing = clip;
      $('an-playing').textContent = `Playing ${clip} (${anim.duration.toFixed(1)} s) on ${model}`;
      run();
    } catch (e) { status(`Preview: ${e.message}`, true); }
  }

  // ------------------------------------------------------------ data
  async function getJSON(url) {
    const r = await fetch(url);
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || r.statusText);
    return doc;
  }
  async function open() {
    if (!st.tables) {
      status('Reading the animation tables...');
      st.tables = await getJSON('/api/animtables');
      st.clips = await getJSON('/api/clips');
      $('an-clips').innerHTML = st.clips.map((c) => `<option value="${esc(c)}">`).join('');
      $('an-table').innerHTML = st.tables.map((t) => `<option value="${esc(t.name)}">${esc(t.name)}${t.changed ? ' (changed)' : ''}`
        + `${t.models.length ? '' : ' (no character)'}</option>`).join('');
      const first = st.tables.find((t) => t.name === 'player_american') || st.tables[0];
      $('an-table').value = first.name;
      status('');
      await pick(first.name);
    }
  }
  async function pick(name) {
    flushUndo();
    st.name = name;
    st.data = await getJSON(`/api/animtable/${encodeURIComponent(name)}`);
    if (!st.docs[name]) st.docs[name] = st.data.table;
    undoBase = JSON.stringify(st.docs[name]);
    const sets = st.docs[name].sets.map((s) => s.name);
    if (!sets.includes(st.set)) st.set = sets.includes('default') ? 'default' : sets[0];
    $('an-model').innerHTML = st.data.models.map((m) => `<option>${esc(m)}</option>`).join('')
      || '<option value="">(no character uses it)</option>';
    draw();
  }

  // ------------------------------------------------------------ drawing
  const flagWords = (v) => {
    const n = parseInt(v, 16) || 0, out = [];
    for (let b = 0; b < 32; b++) {
      const bit = (n >>> b) & 1 ? 2 ** b : 0;
      if (!bit) continue;
      const known = st.data.flags[`0x${bit.toString(16).toUpperCase()}`];
      out.push(known ? known[0] : `0x${bit.toString(16).toUpperCase()}`);
    }
    return out;
  };
  const flagTitle = (v) => {
    const n = parseInt(v, 16) || 0, out = [];
    for (let b = 0; b < 32; b++) {
      const bit = (n >>> b) & 1 ? 2 ** b : 0;
      if (!bit) continue;
      const known = st.data.flags[`0x${bit.toString(16).toUpperCase()}`];
      out.push(`0x${bit.toString(16).toUpperCase()}: ${known ? `${known[0]} (${known[1]})` : 'unknown'}`);
    }
    return out.join('\n') || 'no flags';
  };
  function gameEntry(set, handle) {
    const s = st.data.game.sets.find((x) => x.name === set);
    return s && s.entries.find((e) => e.handle === handle);
  }
  const same = (a, b) => !!a && !!b && (a.anim || '') === (b.anim || '') && +a.priority === +b.priority
    && (parseInt(a.flags, 16) || 0) === (parseInt(b.flags, 16) || 0) && JSON.stringify(a.variants || null) === JSON.stringify(b.variants || null);
  function draw() {
    if (!st.data) return;
    const doc = st.docs[st.name];
    $('an-file').textContent = st.data.file ? `Saved in ${st.data.file}` : 'The game\'s table (no changes yet)';
    $('an-fallback').textContent = doc.fallback ? `Handles it lacks come from ${doc.fallback}.` : '';
    $('an-set').innerHTML = doc.sets.map((s) => `<option ${s.name === st.set ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
    const set = doc.sets.find((s) => s.name === st.set);
    const f = st.filter.toLowerCase();
    $('an-entries').innerHTML = set.entries.map((e, i) => {
      if (f && !e.handle.toLowerCase().includes(f) && !(e.anim || '').toLowerCase().includes(f)) return '';
      const changed = !same(e, gameEntry(set.name, e.handle));
      const clip = e.variants
        ? `<span class="an-variants" title="A random pick from the table's variants">one of ${doc.variants.slice(e.variants.start, e.variants.start + e.variants.count).map(esc).join(', ')}</span>`
        : `<input data-i="${i}" data-k="anim" list="an-clips" spellcheck="false" value="${esc(e.anim)}">`;
      const first = e.variants ? doc.variants[e.variants.start] : e.anim;
      return `<div class="an-row${changed ? ' an-changed' : ''}">
        <span class="an-handle" title="${esc(e.handle)}">${esc(e.handle)}</span>
        ${clip}
        <input data-i="${i}" data-k="priority" type="number" min="0" max="255" value="${+e.priority}" title="Priority: a clip cuts in over a lower one (equal: only over a clip with flag 0x4)">
        <input data-i="${i}" data-k="flags" class="an-flags" value="${esc(e.flags)}" title="${esc(flagTitle(e.flags))}">
        <span class="an-words">${esc(flagWords(e.flags).join(', '))}</span>
        <span class="an-buttons"><button data-a="play" data-clip="${esc(first)}" title="Play it on the model">▶</button>${changed && gameEntry(set.name, e.handle)
          ? `<button data-a="revert" data-i="${i}" title="Back to the game's">↺</button>` : ''}</span></div>`;
    }).join('');
  }

  // ------------------------------------------------------------ editing
  function edited() {
    st.dirty.add(st.name);
    setDirty(true);
    clearTimeout(undoTimer);
    undoTimer = setTimeout(flushUndo, 700);
  }
  // A table's entries before and after a change: one step on the editor's history.
  let undoBase = null, undoTimer = null;
  function flushUndo() {
    clearTimeout(undoTimer);
    undoTimer = null;
    if (undoBase === null || !st.name) return;
    const now = JSON.stringify(st.docs[st.name]);
    if (now === undoBase) return;
    const before = undoBase, after = now, name = st.name;
    undoBase = now;
    const put = async (json) => {
      st.docs[name] = JSON.parse(json);
      st.dirty.add(name);
      setDirty(true);
      if (st.name !== name) { $('an-table').value = name; await pick(name); }
      undoBase = json;
      draw();
    };
    ctx.remember && ctx.remember(`Edited animation table ${name}`, () => put(before), () => put(after));
  }
  $('an-entries').addEventListener('change', (e) => {
    const i = e.target.dataset.i, k = e.target.dataset.k;
    if (i === undefined || !k) return;
    const entry = st.docs[st.name].sets.find((s) => s.name === st.set).entries[+i];
    const v = e.target.value.trim();
    if (k === 'anim') {
      if (!v) return draw();
      if (st.clips && !st.clips.some((c) => c.toLowerCase() === v.toLowerCase())) status(`${v} isn't one of the game's clips (fine if the mod adds it)`, true);
      entry.anim = v;
    } else if (k === 'priority') {
      entry.priority = Math.max(0, Math.min(255, Math.round(+v || 0)));
    } else if (k === 'flags') {
      const n = /^0x[0-9a-f]+$/i.test(v) ? parseInt(v, 16) : /^\d+$/.test(v) ? +v : NaN;
      if (Number.isNaN(n)) { status('Flags are a hex number, like 0x4005', true); return draw(); }
      entry.flags = `0x${n.toString(16).toUpperCase()}`;
    }
    edited();
    draw();
  });
  $('an-entries').addEventListener('click', (e) => {
    const a = e.target.dataset.a;
    if (a === 'play') return play(e.target.dataset.clip);
    if (a === 'revert') {
      const set = st.docs[st.name].sets.find((s) => s.name === st.set);
      const entry = set.entries[+e.target.dataset.i];
      Object.assign(entry, JSON.parse(JSON.stringify(gameEntry(set.name, entry.handle))));
      if (!gameEntry(set.name, entry.handle).variants) delete entry.variants;
      edited();
      draw();
    }
  });
  $('an-table').onchange = () => pick($('an-table').value).catch((err) => status(err.message, true));
  $('an-set').onchange = () => { st.set = $('an-set').value; draw(); };
  $('an-filter').oninput = () => { st.filter = $('an-filter').value; draw(); };
  $('an-model').onchange = () => { if (pv.playing) play(pv.playing); else showModel($('an-model').value); };
  $('an-stop').onclick = () => { if (pv.mixer) pv.mixer.stopAllAction(); pv.playing = null; $('an-playing').textContent = ''; };

  function show(on) {
    $('animations').hidden = !on;
    $('anims-mode').classList.toggle('on', on);
    if (on) open().then(() => { showModel($('an-model').value); run(); }).catch((err) => status(`Animations: ${err.message}`, true));
  }
  $('anims-mode').onclick = () => show($('animations').hidden);
  $('anims-close').onclick = () => show(false);

  // The editor's Save: each changed table.
  function saveBodies() {
    return [...st.dirty].map((name) => ({name, doc: st.docs[name]}));
  }
  function saved(name, file) {
    st.dirty.delete(name);
    const t = st.tables && st.tables.find((x) => x.name === name);
    if (t) t.changed = !!file;
    if (st.name === name && st.data) { st.data.file = file; draw(); }
  }
  return {show, saveBodies, saved, flushUndo};
}
