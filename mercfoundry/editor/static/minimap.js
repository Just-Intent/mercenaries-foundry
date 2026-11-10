// A top-down map of the whole terrain in a corner of the view: hill-shaded
// heights tinted by height, the water areas, where the camera looks (and
// which way), and the player when the live link is on. Click it to look
// there; M hides it. The top is the game's north (-z).
export function createMinimap(ctx) {
  const {$, lookAt, heightAt} = ctx;
  const box = $('minimap');
  const base = document.createElement('canvas');      // the terrain, drawn once per map
  const view = $('minimap-canvas');                    // terrain + overlays, drawn each frame
  const g = view.getContext('2d');
  const st = {meta: null, water: [], shown: true, lastDraw: 0};

  // Height tint: low green, hills brown, peaks pale.
  const STOPS = [[-60, [70, 90, 70]], [0, [92, 124, 72]], [60, [120, 140, 82]], [150, [140, 122, 92]],
                 [300, [150, 140, 128]], [520, [225, 225, 222]]];
  function tint(h) {
    for (let i = 1; i < STOPS.length; i++) {
      if (h <= STOPS[i][0]) {
        const [h0, c0] = STOPS[i - 1], [h1, c1] = STOPS[i];
        const t = Math.max(0, Math.min(1, (h - h0) / (h1 - h0)));
        return c0.map((v, k) => v + (c1[k] - v) * t);
      }
    }
    return STOPS[STOPS.length - 1][1];
  }

  function setTerrain(meta, heights) {
    st.meta = meta;
    const n = meta.size;
    base.width = base.height = n;
    const b = base.getContext('2d');
    const img = b.createImageData(n, n);
    const at = (c, r) => heights[Math.min(n - 1, Math.max(0, r)) * n + Math.min(n - 1, Math.max(0, c))];
    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) {
        // Light from the north-west: brighter on slopes facing it.
        const dx = (at(c + 1, r) - at(c - 1, r)) / (2 * meta.spacing);
        const dz = (at(c, r + 1) - at(c, r - 1)) / (2 * meta.spacing);
        const shade = Math.max(0.45, Math.min(1.35, 1 - (dx + dz) * 0.9));
        const col = tint(at(c, r));
        const i = (r * n + c) * 4;
        img.data[i] = col[0] * shade; img.data[i + 1] = col[1] * shade; img.data[i + 2] = col[2] * shade;
        img.data[i + 3] = 255;
      }
    }
    b.putImageData(img, 0, 0);
    box.hidden = !st.shown;
  }

  // Game (x, z) to minimap pixels, and back.
  const toPx = (x, z) => {
    const m = st.meta, w = view.width;
    return [((x - m.x0) / (m.size * m.spacing)) * w, ((z - m.z0) / (m.size * m.spacing)) * w];
  };
  const fromPx = (px, py) => {
    const m = st.meta, w = view.width;
    return [m.x0 + (px / w) * m.size * m.spacing, m.z0 + (py / w) * m.size * m.spacing];
  };

  function setWater(areas) { st.water = areas; }

  function draw(target, cameraPos, player) {
    if (!st.meta || !st.shown) return;
    const w = view.width;
    g.imageSmoothingEnabled = true;
    g.drawImage(base, 0, 0, w, w);
    g.fillStyle = 'rgba(47, 111, 159, 0.75)';
    for (const pts of st.water) {
      if (pts.length < 3) continue;
      g.beginPath();
      pts.forEach((p, i) => { const [x, y] = toPx(p[0], p[2]); i ? g.lineTo(x, y) : g.moveTo(x, y); });
      g.closePath();
      g.fill();
    }
    // The view: a wedge from where the camera is toward what it looks at.
    const [tx, ty] = toPx(target[0], target[2]);
    const [cx, cy] = toPx(cameraPos[0], cameraPos[2]);
    const ang = Math.atan2(ty - cy, tx - cx);
    const s = w / 240;                     // marker sizes in screen pixels (the canvas is shown at 240)
    g.fillStyle = 'rgba(255, 255, 255, 0.4)';
    g.strokeStyle = 'rgba(0, 0, 0, 0.6)';
    g.lineWidth = s;
    g.beginPath();
    g.moveTo(cx, cy);
    g.arc(cx, cy, Math.max(22 * s, Math.hypot(tx - cx, ty - cy) * 1.4), ang - 0.45, ang + 0.45);
    g.closePath();
    g.fill();
    g.stroke();
    g.fillStyle = '#ffcc33';
    g.lineWidth = 1.5 * s;
    g.beginPath(); g.arc(tx, ty, 4.5 * s, 0, Math.PI * 2); g.fill(); g.stroke();
    if (player) {
      const [px, py] = toPx(player.position[0], player.position[2]);
      g.fillStyle = '#ff4d4d';
      g.strokeStyle = '#000';
      g.lineWidth = 1.5 * s;
      g.beginPath(); g.arc(px, py, 5 * s, 0, Math.PI * 2); g.fill(); g.stroke();
      if (player.forward) {
        g.strokeStyle = '#ff4d4d';
        g.lineWidth = 2 * s;
        g.beginPath(); g.moveTo(px, py); g.lineTo(px + player.forward[0] * 14 * s, py + player.forward[2] * 14 * s); g.stroke();
      }
    }
  }

  view.addEventListener('click', (e) => {
    if (!st.meta) return;
    const r = view.getBoundingClientRect();
    const [x, z] = fromPx((e.clientX - r.left) * view.width / r.width, (e.clientY - r.top) * view.height / r.height);
    lookAt([x, heightAt(x, z), z], 120);
  });
  function toggle() {
    st.shown = !st.shown;
    box.hidden = !st.shown || !st.meta;
  }
  $('minimap-hide').onclick = toggle;
  return {setTerrain, setWater, draw, toggle};
}
