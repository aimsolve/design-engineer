// CAD look for the assembly viewer: (1) baked feature edges drawn per instance, (2) a screen-space pass = ambient occlusion in gaps +
// silhouette outlines + line thickness, (3) procedural galvanised-sheet texture. Plain browser module, only depends on three.
//
//   const fx = createFx(THREE, renderer, scene, camera);   fx.render() instead of renderer.render(scene, camera)
//   fx.set({ edges: 0..1, thick: 1..4 px, ao: 0..1, aoRadius: mm, edgeColor: 0xRRGGBB })
//   attachEdges(THREE, {byPart, url, lineMat, onProgress, invalidate})  -> loads edges.json/.bin (made by step-import/scripts/bake_edges.py)
//   makeSpangle(THREE) -> DataTexture; spangleize(material, tex, strength)  (box-projected, no UVs needed)
//
// Why one scene render + two fullscreen passes: the scene goes to a multisampled float target with a depth texture. The AO pass rebuilds
// view-space positions from that depth (no second scene render for normals; normals come from depth derivatives). The composite pass blurs
// the AO with a depth-aware filter, adds silhouette outlines (Laplacian of 1/z: zero on any plane, big on a depth jump) and dilates the
// baked line pixels to the requested thickness. Baked lines are drawn into the scene target as an alpha=0.5 marker (colour untouched),
// so their strength and thickness are decided in the composite, not by the 1 px WebGL line.

const FS_VERT = `varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`;

const AO_FRAG = `
varying vec2 vUv;
uniform sampler2D tDepth;
uniform mat4 uProjInv;
uniform float uProj11, uResY, uRadius, uMinPx, uMaxPx, uGain;
uniform vec2 uTexel;
#define N_SAMPLES 12
vec3 viewPos(vec2 uv){ float d = texture2D(tDepth, uv).x; vec4 c = uProjInv * vec4(uv * 2.0 - 1.0, d * 2.0 - 1.0, 1.0); return c.xyz / c.w; }
float ign(vec2 p){ return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }
void main(){
  float d0 = texture2D(tDepth, vUv).x;
  if (d0 >= 0.99999) { gl_FragColor = vec4(1.0); return; }
  vec3 P = viewPos(vUv);
  vec3 Pl = viewPos(vUv - vec2(uTexel.x, 0.0)), Pr = viewPos(vUv + vec2(uTexel.x, 0.0));
  vec3 Pd = viewPos(vUv - vec2(0.0, uTexel.y)), Pu = viewPos(vUv + vec2(0.0, uTexel.y));
  vec3 dx = abs(Pr.z - P.z) < abs(P.z - Pl.z) ? Pr - P : P - Pl;
  vec3 dy = abs(Pu.z - P.z) < abs(P.z - Pd.z) ? Pu - P : P - Pd;
  vec3 N = normalize(cross(dx, dy));
  if (N.z < 0.0) N = -N;
  float z = -P.z;
  float rpx = clamp(uRadius * uProj11 * uResY / (2.0 * z), uMinPx, uMaxPx);
  float worldR = rpx * 2.0 * z / (uProj11 * uResY);
  float rot = ign(gl_FragCoord.xy) * 6.2831853;
  float occ = 0.0;
  for (int i = 0; i < N_SAMPLES; i++) {
    float fi = (float(i) + 0.5) / float(N_SAMPLES);
    float a = rot + float(i) * 2.399963;
    vec2 off = vec2(cos(a), sin(a)) * (sqrt(fi) * rpx) * uTexel;
    vec3 v = viewPos(vUv + off) - P;
    float l = length(v);
    float w = 1.0 - smoothstep(0.55, 1.0, l / worldR);
    occ += max(0.0, dot(N, v) / max(l, 1e-4) - 0.12) * w;
  }
  occ /= float(N_SAMPLES);
  gl_FragColor = vec4(vec3(1.0 - clamp(occ * uGain, 0.0, 1.0)), 1.0);
}`;

const COMP_FRAG = `
varying vec2 vUv;
uniform sampler2D tColor, tDepth, tAO;
uniform vec2 uTexel;
uniform float uNear, uFar, uEdge, uThick, uAoOn, uSilThr;
uniform vec3 uEdgeCol;
float lin(vec2 uv){ float d = texture2D(tDepth, uv).x; return uNear * uFar / (uFar - d * (uFar - uNear)); }
void main(){
  vec4 c4 = texture2D(tColor, vUv);
  vec3 col = c4.rgb;
  float z0 = lin(vUv);
  if (uAoOn > 0.5) {
    float s = 0.0, ws = 0.0;
    for (int y = -2; y <= 2; y++) for (int x = -2; x <= 2; x++) {
      vec2 o = vUv + vec2(float(x), float(y)) * uTexel;
      float z = lin(o);
      float w = 1.0 / (1.0 + 300.0 * abs(z - z0) / z0);
      s += texture2D(tAO, o).r * w; ws += w;
    }
    col *= s / ws;
  }
  float e = 0.0;
  if (uEdge > 0.0) {
    // baked lines: alpha 0.5 marks a line pixel; dilate to the requested thickness (thick 1 = as drawn)
    float rho = (uThick - 1.0) * 0.5;
    float cov = 0.0;
    for (int y = -2; y <= 2; y++) for (int x = -2; x <= 2; x++) {
      float w = clamp(rho + 1.0 - length(vec2(float(x), float(y))), 0.0, 1.0);
      if (w <= 0.0) continue;
      float a = texture2D(tColor, vUv + vec2(float(x), float(y)) * uTexel).a;
      cov = max(cov, w * clamp((1.0 - a) * 2.0, 0.0, 1.0));
    }
    // silhouettes: centre is FARTHER than its neighbours along an axis by more than a plane can explain
    vec2 st = uTexel * max(1.0, floor(uThick + 0.5));
    float ic = 1.0 / z0;
    float lx = 1.0 / lin(vUv + vec2(st.x, 0.0)) + 1.0 / lin(vUv - vec2(st.x, 0.0)) - 2.0 * ic;
    float ly = 1.0 / lin(vUv + vec2(0.0, st.y)) + 1.0 / lin(vUv - vec2(0.0, st.y)) - 2.0 * ic;
    float sil = smoothstep(uSilThr, uSilThr * 2.5, max(lx, ly) / ic);
    e = max(cov, sil) * uEdge;
  }
  col = mix(col, uEdgeCol, e);
  gl_FragColor = vec4(col, 1.0);
  gl_FragColor = linearToOutputTexel(gl_FragColor);
}`;

export function createFx(THREE, renderer, scene, camera) {
  const P = { edges: 0.8, thick: 1, ao: 0.6, aoRadius: 45, edgeColor: 0x0b0d10, silThr: 0.004 };
  const fx = { supported: true, params: P, ms: { scene: 0 }, stats: { calls: 0, triangles: 0 } };
  const gl = renderer.getContext();
  if (!(gl instanceof WebGL2RenderingContext)) { fx.supported = false; }
  const half = renderer.extensions.has("EXT_color_buffer_float") || renderer.extensions.has("EXT_color_buffer_half_float");
  let rt = null, aoRT = null, depthTex = null;
  const quadGeo = new THREE.BufferGeometry();
  quadGeo.setAttribute("position", new THREE.Float32BufferAttribute([-1, -1, 0, 3, -1, 0, -1, 3, 0], 3));
  quadGeo.setAttribute("uv", new THREE.Float32BufferAttribute([0, 0, 2, 0, 0, 2], 2));
  const quadCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const mkPass = (frag, uniforms) => {
    const m = new THREE.ShaderMaterial({ vertexShader: FS_VERT, fragmentShader: frag, uniforms, depthTest: false, depthWrite: false, toneMapped: false });
    const mesh = new THREE.Mesh(quadGeo, m); mesh.frustumCulled = false;
    const sc = new THREE.Scene(); sc.add(mesh);
    return { m, sc };
  };
  const u = (v) => ({ value: v });
  const aoPass = mkPass(AO_FRAG, { tDepth: u(null), uProjInv: u(new THREE.Matrix4()), uProj11: u(1), uResY: u(1), uRadius: u(36), uMinPx: u(5), uMaxPx: u(70), uGain: u(3), uTexel: u(new THREE.Vector2()) });
  const compPass = mkPass(COMP_FRAG, { tColor: u(null), tDepth: u(null), tAO: u(null), uTexel: u(new THREE.Vector2()), uNear: u(1), uFar: u(10), uEdge: u(0), uThick: u(1), uAoOn: u(0),
    uSilThr: u(0.004), uEdgeCol: u(new THREE.Color(0x0b0d10)) });

  function build() {
    const v = renderer.getDrawingBufferSize(new THREE.Vector2());
    const w = Math.max(2, v.x | 0), h = Math.max(2, v.y | 0);
    if (rt && rt.width === w && rt.height === h) return;
    if (rt) { rt.dispose(); aoRT.dispose(); depthTex.dispose(); }
    depthTex = new THREE.DepthTexture(w, h);
    depthTex.type = THREE.UnsignedIntType; depthTex.minFilter = depthTex.magFilter = THREE.NearestFilter;
    rt = new THREE.WebGLRenderTarget(w, h, { type: half ? THREE.HalfFloatType : THREE.UnsignedByteType, samples: 4, depthTexture: depthTex, minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter, generateMipmaps: false });
    aoRT = new THREE.WebGLRenderTarget(w, h, { type: THREE.UnsignedByteType, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, depthBuffer: false, generateMipmaps: false });
  }
  fx.resize = () => { if (fx.supported) { try { build(); } catch (e) { console.warn("fx: post pipeline unavailable", e); fx.supported = false; } } };
  fx.set = (o) => { Object.assign(P, o); };
  const active = () => fx.supported && (P.edges > 0 || P.ao > 0);
  fx.active = active;

  fx.render = () => {
    if (!active()) { renderer.setRenderTarget(null); renderer.info.autoReset = true; renderer.render(scene, camera); fx.stats = { calls: renderer.info.render.calls, triangles: renderer.info.render.triangles }; return; }
    try { build(); } catch (e) { console.warn("fx: post pipeline unavailable", e); fx.supported = false; return fx.render(); }
    const v = new THREE.Vector2(1 / rt.width, 1 / rt.height);
    renderer.info.autoReset = false; renderer.info.reset();
    renderer.setRenderTarget(rt);
    renderer.render(scene, camera);
    fx.stats = { calls: renderer.info.render.calls, triangles: renderer.info.render.triangles };
    const useAo = P.ao > 0;
    if (useAo) {
      const q = aoPass.m.uniforms;
      q.tDepth.value = depthTex; q.uProjInv.value.copy(camera.projectionMatrixInverse); q.uProj11.value = camera.projectionMatrix.elements[5];
      q.uResY.value = rt.height; q.uRadius.value = P.aoRadius; q.uTexel.value.copy(v); q.uGain.value = 1 + 8 * P.ao;
      q.uMinPx.value = 4 * (rt.height / 900); q.uMaxPx.value = 70 * (rt.height / 900);
      renderer.setRenderTarget(aoRT); renderer.render(aoPass.sc, quadCam);
    }
    const c = compPass.m.uniforms;
    c.tColor.value = rt.texture; c.tDepth.value = depthTex; c.tAO.value = aoRT.texture; c.uTexel.value.copy(v);
    c.uNear.value = camera.near; c.uFar.value = camera.far; c.uEdge.value = P.edges; c.uThick.value = Math.max(1, P.thick * (rt.height / 900 > 1.5 ? 1.6 : 1));
    c.uAoOn.value = useAo ? 1 : 0; c.uSilThr.value = P.silThr; c.uEdgeCol.value.set(P.edgeColor);
    renderer.setRenderTarget(null); renderer.render(compPass.sc, quadCam);
    renderer.info.autoReset = true;
  };
  fx.dispose = () => { if (rt) { rt.dispose(); aoRT.dispose(); depthTex.dispose(); } };
  return fx;
}

// ---------------- baked edges ----------------
// Line material for the post pipeline: writes ONLY alpha=0.5 (rgb blend = keep destination), the composite pass turns that marker into a line of the
// requested strength/thickness. lineMatDirect is the fallback when the pipeline is unavailable: a plain dark 1 px line.
export function makeLineMaterials(THREE) {
  const marker = new THREE.ShaderMaterial({
    vertexShader: "void main(){ gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
    fragmentShader: "void main(){ gl_FragColor = vec4(0.0, 0.0, 0.0, 0.5); }",
    depthWrite: false, blending: THREE.CustomBlending, blendEquation: THREE.AddEquation, blendSrc: THREE.ZeroFactor, blendDst: THREE.OneFactor,
    blendEquationAlpha: THREE.AddEquation, blendSrcAlpha: THREE.OneFactor, blendDstAlpha: THREE.ZeroFactor,
  });
  const direct = new THREE.LineBasicMaterial({ color: 0x0b0d10, transparent: true, opacity: 0.8, depthWrite: false });
  return { marker, direct };
}

export async function attachEdges(THREE, o) {
  // o: {url (edges.json), byPart, lineMat, onDone(n), invalidate()} ; builds line objects in small batches so the page stays responsive
  const meta = await (await fetch(o.url)).json();
  const bin = await (await fetch(o.url.replace(/\.json(?=\?|$)/, ".bin"))).arrayBuffer();
  const geos = {};
  const geoOf = (id) => {
    if (geos[id]) return geos[id];
    const m = meta.parts[id]; if (!m) return null;
    const [nv, ne, vo, io, ib] = m;
    const ibuf = new THREE.InterleavedBuffer(new Int16Array(bin.slice(vo, vo + nv * 8)), 4);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.InterleavedBufferAttribute(ibuf, 3, 0, true));
    g.setIndex(new THREE.BufferAttribute(ib === 4 ? new Uint32Array(bin.slice(io, io + ne * 8)) : new Uint16Array(bin.slice(io, io + ne * 4)), 1));
    g.computeBoundingSphere();
    return (geos[id] = g);
  };
  const work = [];
  for (const id of Object.keys(o.byPart)) for (const mesh of o.byPart[id]) if (!mesh.userData.hasEdges && meta.parts[id]) work.push(mesh);
  let i = 0, made = 0;
  await new Promise((done) => {
    const step = () => {
      const t0 = performance.now();
      while (i < work.length && performance.now() - t0 < 8) {
        const mesh = work[i++], g = geoOf(mesh.userData.part);
        if (!g) continue;
        const ln = new THREE.LineSegments(g, o.lineMat());
        ln.matrixAutoUpdate = false; ln.userData.edge = true; ln.name = ""; ln.visible = o.visible ? o.visible() : true; if (o.onLine) o.onLine(ln);
        mesh.add(ln); mesh.userData.hasEdges = true; made++;
      }
      if (o.onProgress) o.onProgress(i / Math.max(1, work.length));
      o.invalidate();
      if (i < work.length) setTimeout(step, 0); else done();
    };
    step();
  });
  return { lines: made, bytes: bin.byteLength, geos: Object.keys(geos).length };
}

// ---------------- galvanised sheet ----------------
export function makeSpangle(THREE, size = 256, cells = 80) {
  // tileable Voronoi: every cell a random brightness, thin darker seams
  let s = 1234567;
  const rnd = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  const pts = []; for (let i = 0; i < cells; i++) pts.push([rnd() * size, rnd() * size, 0.72 + rnd() * 0.5]);
  const data = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    let d1 = 1e9, d2 = 1e9, b = 1;
    for (const p of pts) {
      let dx = Math.abs(x - p[0]), dy = Math.abs(y - p[1]); if (dx > size / 2) dx = size - dx; if (dy > size / 2) dy = size - dy;
      const d = dx * dx + dy * dy;
      if (d < d1) { d2 = d1; d1 = d; b = p[2]; } else if (d < d2) d2 = d;
    }
    const seam = Math.min(1, (Math.sqrt(d2) - Math.sqrt(d1)) / 2.2);
    const v = Math.max(0, Math.min(255, Math.round(255 * Math.min(1, b * (0.86 + 0.14 * seam) * 0.86))));
    const k = (y * size + x) * 4; data[k] = data[k + 1] = data[k + 2] = v; data[k + 3] = 255;
  }
  const t = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  t.wrapS = t.wrapT = THREE.RepeatWrapping; t.minFilter = THREE.LinearMipmapLinearFilter; t.magFilter = THREE.LinearFilter;
  t.generateMipmaps = true; t.anisotropy = 4; t.colorSpace = THREE.NoColorSpace; t.needsUpdate = true;
  return t;
}

// Box-projected (triplanar) spangle on a MeshStandardMaterial without UVs. Coordinates = position in the part's own frame scaled to mm
// (so the texture moves with an animated part). `mmPerTile`: size of one texture tile in mm.
export function spangleize(material, tex, strength = 0.35, mmPerTile = 70) {
  const k = (1 / mmPerTile).toFixed(6);
  material.onBeforeCompile = (sh) => {
    sh.uniforms.uSpangle = { value: tex };
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vSpP; varying vec3 vSpN;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvSpP = transformed * vec3(length(modelMatrix[0].xyz), length(modelMatrix[1].xyz), length(modelMatrix[2].xyz)); vSpN = objectNormal;");
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vSpP; varying vec3 vSpN; uniform sampler2D uSpangle;")
      .replace("#include <map_fragment>", `#include <map_fragment>
      vec3 spw = pow(abs(vSpN), vec3(4.0)); spw /= (spw.x + spw.y + spw.z + 1e-5);
      float spv = texture2D(uSpangle, vSpP.yz * ${k}).r * spw.x + texture2D(uSpangle, vSpP.xz * ${k}).r * spw.y + texture2D(uSpangle, vSpP.xy * ${k}).r * spw.z;
      diffuseColor.rgb *= mix(1.0, spv * 1.2, ${strength.toFixed(3)});`)
      .replace("#include <roughnessmap_fragment>", "#include <roughnessmap_fragment>\n roughnessFactor = clamp(roughnessFactor * (0.8 + 0.5 * spv), 0.05, 1.0);");
  };
  material.customProgramCacheKey = () => "spangle" + strength + mmPerTile;
  return material;
}
