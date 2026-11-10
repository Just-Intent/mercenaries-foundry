// The editor's live link to a running game (through the Foundry Console
// plugin): the player's position on the map, jumping to the player, bringing
// the player to the view, and object moves, turns and removals sent to the
// game as they're made (the mod's saved files still decide what the game has
// next time it starts).
import * as THREE from 'three';

export function createLive(ctx) {
  const {scene, threePos, heightAt, status, $} = ctx;
  const st = {on: false, connected: false, player: null, pending: new Map(), timer: null};

  // The player: an arrow pointing the way they face.
  const marker = new THREE.Group();
  const mat = new THREE.MeshBasicMaterial({color: 0x30e0ff, depthTest: false});
  const body = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.35, 2.2, 10).translate(0, 1.1, 0), mat);
  const tip = new THREE.Mesh(new THREE.ConeGeometry(0.7, 1.6, 12).rotateX(Math.PI / 2).translate(0, 1.4, 0.9), mat);
  body.renderOrder = tip.renderOrder = 20;
  marker.add(body, tip);
  marker.visible = false;
  scene.add(marker);

  async function poll() {
    try {
      const r = await fetch('/api/live/state');
      const s = await r.json();
      st.connected = s.connected;
      st.player = s.player;
      if (s.loaded && JSON.stringify(s.loaded) !== JSON.stringify(st.loaded)) {
        st.loaded = s.loaded;
        ctx.onLoaded && ctx.onLoaded(s.loaded);
      }
      for (const res of s.results || []) {
        status(`Game: ${res}`);
        // A short log of the game's replies that later messages don't hide.
        const li = document.createElement('li');
        li.textContent = res;
        $('game-log').prepend(li);
        while ($('game-log').children.length > 12) $('game-log').lastChild.remove();
        $('game-log-title').hidden = false;
      }
    } catch {
      st.connected = false;
    }
    show();
  }

  function show() {
    const btn = $('live');
    btn.classList.toggle('on', st.on && st.connected);
    btn.textContent = !st.connected ? 'Live (no game)' : st.on ? 'Live: on' : 'Live: off';
    btn.title = st.connected
      ? 'Send moves, turns and removals to the running game as you make them'
      : 'Start the game with Foundry Console on; it finds this editor by itself';
    $('to-player').disabled = $('player-here').disabled = !(st.connected && st.player);
    if (st.connected && st.player) {
      const p = st.player.position, f = st.player.forward;
      marker.position.copy(threePos(p));
      marker.rotation.y = Math.atan2(f[0], -f[1]);     // game forward (x, z) -> three (x, -z)
      marker.visible = true;
    } else {
      marker.visible = false;
    }
  }

  async function send(commands) {
    if (!st.on || !st.connected || !commands.length) return;
    try {
      await fetch('/api/live/send', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                     body: JSON.stringify({commands})});
    } catch (e) {
      status(`Not sent to the game: ${e.message}`, true);
    }
  }

  // Where the game may have the object: as the game data has it, as the
  // editor had it when it loaded (a built mod may have moved it), and as
  // last sent.
  const places = (rec) => [rec.original.position, rec.loadedAt, rec.liveAt].filter(Boolean).slice(0, 4);

  // Drags send once, when they settle (key: what is being dragged).
  function later(key, fn) {
    clearTimeout(st.pending.get(key));
    st.pending.set(key, setTimeout(() => { st.pending.delete(key); fn(); }, 250));
  }

  function objectMoved(rec) {
    if (rec.context) return;
    if (rec.copyName) return moveNamed(rec, rec.copyName, rec.position, ctx.heading(rec.rotation));
    later(rec, () => {
      send([{op: 'move', name: rec.name, model: rec.model, from: places(rec),
             position: rec.position, heading: ctx.heading(rec.rotation)}]);
      if (st.on && st.connected) rec.liveAt = rec.position;
    });
  }

  function objectRemoved(rec) {
    if (rec.context) return;
    if (rec.copyName) return removeNamed(rec.copyName);
    send([{op: 'remove', name: rec.name, model: rec.model, from: places(rec)}]);
  }

  // Things the game can spawn by name (squad members, copies of named
  // objects): spawned live, then moved and removed by their new names.
  function spawn(template, name, position, heading) {
    if (!template || !name || /\s/.test(template + name)) return;
    send([{op: 'spawn', template, name, position, heading: heading || 0}]);
  }
  // A copy of anything else (unnamed scenery): the game finds the source as a
  // move would and spawns its properties under the new name.
  function copy(src, name, position, heading) {
    if (!name || /\s/.test(name) || src.context) return;
    send([{op: 'copy', name: src.name, model: src.model, from: places(src), as: name,
           position, heading: heading || 0}]);
  }
  function moveNamed(key, name, position, heading) {
    later(key, () => send([{op: 'move', name, model: '', from: [], position, heading: heading || 0}]));
  }
  function removeNamed(name) {
    send([{op: 'remove', name, model: '', from: []}]);
  }

  $('live').onclick = () => {
    if (!st.connected) return status('Start the game with Foundry Console on, then try again.', true);
    st.on = !st.on;
    show();
    status(st.on ? 'Live: moves, turns, removals and copies now go to the game too (squads need a rebuild).' : 'Live off');
  };
  $('to-player').onclick = () => st.player && ctx.lookAt(st.player.position, 40);
  $('player-here').onclick = () => {
    if (!st.player) return;
    const t = ctx.target();
    const p = [t[0], heightAt(t[0], t[2]) + 1.5, t[2]];
    st.on || status('Teleporting the player (Live is off for edits).');
    fetch('/api/live/send', {method: 'POST', headers: {'Content-Type': 'application/json'},
                             body: JSON.stringify({commands: [{op: 'teleport', position: p}]})});
  };

  st.timer = setInterval(poll, 500);
  poll();
  // Ask the game which templates are loaded where the player is (for the
  // squad picker); the answer arrives through poll() a moment later.
  function askLoaded() {
    if (!st.connected) return;
    fetch('/api/live/send', {method: 'POST', headers: {'Content-Type': 'application/json'},
                             body: JSON.stringify({commands: [{op: 'loaded'}]})});
  }

  return {objectMoved, objectRemoved, spawn, copy, moveNamed, removeNamed, askLoaded,
          player: () => (st.on && st.connected ? st.player : null),
          loaded: () => st.loaded || null, isOn: () => st.on && st.connected};
}
