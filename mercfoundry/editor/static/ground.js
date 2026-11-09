// The ground as the game draws it: up to 16 texture layers, each tiled at its
// own scale and projection, blended bottom to top by its painted opacity, and
// multiplied by the map's baked lighting. All layer textures go in one texture
// array and all paint in another, so it takes two textures.
import * as THREE from 'three';

const TEX_SIZE = 256;          // each layer texture, resampled (it tiles, so this is plenty)
const LIGHT_SCALE = 2.0;       // baked colours are stored at half brightness (128 = unlit)

function loadImage(url) {
  return new Promise((ok, bad) => {
    const img = new Image();
    img.onload = () => ok(img);
    img.onerror = () => bad(new Error(`couldn't load ${url}`));
    img.src = url;
  });
}

async function layerArray(layers) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = TEX_SIZE;
  const g = canvas.getContext('2d', {willReadFrequently: true});
  const data = new Uint8Array(TEX_SIZE * TEX_SIZE * 4 * layers.length);
  const images = await Promise.all(layers.map((l) =>
    loadImage(`/api/texture/${encodeURIComponent(l.texture)}.png`).catch(() => null)));
  images.forEach((img, i) => {
    g.fillStyle = '#6b6b5a';
    g.fillRect(0, 0, TEX_SIZE, TEX_SIZE);
    if (img) g.drawImage(img, 0, 0, TEX_SIZE, TEX_SIZE);
    data.set(g.getImageData(0, 0, TEX_SIZE, TEX_SIZE).data, i * TEX_SIZE * TEX_SIZE * 4);
  });
  const tex = new THREE.DataArrayTexture(data, TEX_SIZE, TEX_SIZE, layers.length);
  tex.format = THREE.RGBAFormat;
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = true;
  tex.anisotropy = 4;
  tex.needsUpdate = true;
  return tex;
}

const VERTEX = /* glsl */ `
in vec3 light;
in vec2 paintUv;
out vec3 vLight;
out vec2 vPaint;
out vec3 vWorld;
void main() {
  vLight = light;
  vPaint = paintUv;
  vec4 w = modelMatrix * vec4(position, 1.0);
  vWorld = w.xyz;
  gl_Position = projectionMatrix * viewMatrix * w;
}`;

const FRAGMENT = /* glsl */ `
precision highp float;
precision highp sampler2DArray;
uniform sampler2DArray layers;
uniform sampler2DArray paint;
uniform float scales[16];
uniform int axes[16];
uniform int count;
uniform float lightScale;
uniform vec3 fogColor;
uniform float fogNear;
uniform float fogFar;
in vec3 vLight;
in vec2 vPaint;
in vec3 vWorld;
out vec4 outColor;
void main() {
  vec3 g = vec3(vWorld.x, vWorld.y, -vWorld.z);      // game axes
  vec3 c = vec3(0.35, 0.37, 0.3);
  for (int i = 0; i < 16; i++) {
    if (i >= count) break;
    float a = texture(paint, vec3(vPaint, float(i))).r;
    if (a < 0.004) continue;
    // Projection: 0 from above; 4 and 5 from the side (cliffs, mountains).
    vec2 uv = axes[i] == 4 ? g.zy : axes[i] == 5 ? g.xy : g.xz;
    c = mix(c, texture(layers, vec3(uv / scales[i], float(i))).rgb, a);
  }
  c *= vLight * lightScale;
  float d = length(vWorld - cameraPosition);
  c = mix(c, fogColor, smoothstep(fogNear, fogFar, d));
  outColor = vec4(c, 1.0);
}`;

// Give a terrain mesh (built from the height grid, size x size vertices,
// row by row) the game's ground. Resolves when done; leaves the mesh as it
// was if anything is missing.
export async function applyGround(mesh, map, fog) {
  const r = await fetch(`/api/look/${map}.json`);
  if (!r.ok) return false;
  const look = await r.json();
  const [paint, light, layers] = await Promise.all([
    fetch(`/api/look/${map}.paint`).then((x) => x.arrayBuffer()),
    fetch(`/api/look/${map}.light`).then((x) => x.arrayBuffer()),
    layerArray(look.layers),
  ]);
  const n = look.layers.length, w = look.paint_size, size = look.size;
  const paintTex = new THREE.DataArrayTexture(new Uint8Array(paint), w, w, n);
  paintTex.format = THREE.RedFormat;
  paintTex.unpackAlignment = 1;           // rows of 513 bytes
  paintTex.minFilter = paintTex.magFilter = THREE.LinearFilter;
  paintTex.needsUpdate = true;

  const geo = mesh.geometry;
  const lightAttr = new Float32Array(size * size * 3);
  const lightBytes = new Uint8Array(light);
  const lit = look.lit;
  for (let i = 0; i < size * size * 3; i++) lightAttr[i] = lit ? lightBytes[i] / 255 : 0.5;
  const uv = new Float32Array(size * size * 2);
  for (let row = 0; row < size; row++) {
    for (let col = 0; col < size; col++) {
      const i = row * size + col;
      uv[2 * i] = (col + 0.5) / w;
      uv[2 * i + 1] = (row + 0.5) / w;
    }
  }
  geo.setAttribute('light', new THREE.BufferAttribute(lightAttr, 3));
  geo.setAttribute('paintUv', new THREE.BufferAttribute(uv, 2));
  const scales = look.layers.map((l) => l.scale || 16).concat(Array(16).fill(16)).slice(0, 16);
  const axes = look.layers.map((l) => l.axis || 0).concat(Array(16).fill(0)).slice(0, 16);
  mesh.material = new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3,
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    uniforms: {
      layers: {value: layers}, paint: {value: paintTex}, scales: {value: scales}, axes: {value: axes},
      count: {value: n}, lightScale: {value: LIGHT_SCALE},
      fogColor: {value: fog.color}, fogNear: {value: fog.near}, fogFar: {value: fog.far},
    },
  });
  return true;
}
