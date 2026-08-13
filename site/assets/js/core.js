/* ==========================================================================
   JARVIS Core — 3D Reactor
   Raymarching-Shader in purem WebGL. Keine Library, kein Build-Schritt.
   Fällt automatisch auf eine CSS-Animation zurück, wenn WebGL fehlt.
   ========================================================================== */

(function () {
  "use strict";

  var canvas = document.getElementById("core-canvas");
  var fallback = document.querySelector(".core-fallback");
  if (!canvas) return;

  function useFallback() {
    if (canvas) canvas.style.display = "none";
    if (fallback) fallback.classList.add("is-on");
  }

  var gl = null;
  try {
    var attribs = { alpha: true, premultipliedAlpha: false, antialias: false, depth: false, powerPreference: "high-performance" };
    gl = canvas.getContext("webgl", attribs) || canvas.getContext("experimental-webgl", attribs);
  } catch (e) {
    gl = null;
  }
  if (!gl) { useFallback(); return; }

  /* ---------------------------------------------------------------- Shader */

  var VERT = [
    "attribute vec2 a_pos;",
    "void main(){ gl_Position = vec4(a_pos, 0.0, 1.0); }"
  ].join("\n");

  var FRAG = [
    "precision highp float;",
    "uniform vec2  u_res;",
    "uniform float u_time;",
    "uniform vec2  u_mouse;",
    "uniform vec2  u_shift;",
    "uniform float u_fade;",

    "mat2 rot(float a){ float s = sin(a), c = cos(a); return mat2(c, -s, s, c); }",

    "float hash21(vec2 p){",
    "  p = fract(p * vec2(123.34, 456.21));",
    "  p += dot(p, p + 45.32);",
    "  return fract(p.x * p.y);",
    "}",

    "float hash13(vec3 p){",
    "  p = fract(p * 0.3183099 + vec3(0.71, 0.113, 0.419));",
    "  p *= 17.0;",
    "  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));",
    "}",

    "float vnoise(vec3 x){",
    "  vec3 i = floor(x);",
    "  vec3 f = fract(x);",
    "  f = f * f * (3.0 - 2.0 * f);",
    "  float n000 = hash13(i + vec3(0.0, 0.0, 0.0));",
    "  float n100 = hash13(i + vec3(1.0, 0.0, 0.0));",
    "  float n010 = hash13(i + vec3(0.0, 1.0, 0.0));",
    "  float n110 = hash13(i + vec3(1.0, 1.0, 0.0));",
    "  float n001 = hash13(i + vec3(0.0, 0.0, 1.0));",
    "  float n101 = hash13(i + vec3(1.0, 0.0, 1.0));",
    "  float n011 = hash13(i + vec3(0.0, 1.0, 1.0));",
    "  float n111 = hash13(i + vec3(1.0, 1.0, 1.0));",
    "  return mix(mix(mix(n000, n100, f.x), mix(n010, n110, f.x), f.y),",
    "             mix(mix(n001, n101, f.x), mix(n011, n111, f.x), f.y), f.z);",
    "}",

    "float sdTorus(vec3 p, float R, float r){",
    "  vec2 q = vec2(length(p.xz) - R, p.y);",
    "  return length(q) - r;",
    "}",

    "vec2 opU(vec2 a, vec2 b){ return (a.x < b.x) ? a : b; }",

    /* x = Distanz, y = Material-ID */
    "vec2 map(vec3 p){",
    "  float t = u_time;",

    "  vec3 q = p;",
    "  q.xz *= rot(t * 0.16);",
    "  q.xy *= rot(t * 0.11);",
    "  float n = vnoise(q * 3.2 + vec3(0.0, t * 0.45, 0.0));",
    "  float n2 = vnoise(q * 7.0 - vec3(t * 0.6, 0.0, 0.0));",
    "  float core = length(p) - (0.585 + 0.055 * n + 0.022 * n2 + 0.018 * sin(t * 1.7));",
    "  vec2 res = vec2(core, 1.0);",

    "  vec3 r1 = p;",
    "  r1.yz *= rot(0.52);",
    "  r1.xz *= rot(-t * 0.34);",
    "  res = opU(res, vec2(sdTorus(r1, 1.00, 0.013), 2.0));",

    "  vec3 r2 = p;",
    "  r2.xy *= rot(1.18);",
    "  r2.xz *= rot(t * 0.21);",
    "  float a2 = atan(r2.z, r2.x);",
    "  float dash = 0.005 + 0.017 * smoothstep(0.1, 0.8, sin(a2 * 13.0 + t * 1.1));",
    "  res = opU(res, vec2(sdTorus(r2, 1.33, dash), 3.0));",

    "  vec3 r3 = p;",
    "  r3.yz *= rot(-0.30);",
    "  r3.xz *= rot(t * 0.13);",
    "  res = opU(res, vec2(sdTorus(r3, 1.62, 0.006), 2.0));",

    "  return res;",
    "}",

    "vec3 calcNormal(vec3 p){",
    "  vec2 e = vec2(0.0016, 0.0);",
    "  return normalize(vec3(",
    "    map(p + e.xyy).x - map(p - e.xyy).x,",
    "    map(p + e.yxy).x - map(p - e.yxy).x,",
    "    map(p + e.yyx).x - map(p - e.yyx).x));",
    "}",

    "vec3 matColor(float id){",
    "  if (id > 2.5) return vec3(1.00, 0.66, 0.26);",
    "  if (id > 1.5) return vec3(0.36, 0.92, 0.90);",
    "  return vec3(0.30, 0.90, 0.88);",
    "}",

    "vec3 starfield(vec3 rd){",
    "  vec2 uv = vec2(atan(rd.z, rd.x) * 0.15915, rd.y * 0.5 + 0.5) * vec2(190.0, 105.0);",
    "  vec2 gid = floor(uv);",
    "  vec2 f = fract(uv) - 0.5;",
    "  float r = hash21(gid);",
    "  float on = step(0.976, r);",
    "  vec2 off = (vec2(hash21(gid + 7.3), hash21(gid + 19.1)) - 0.5) * 0.55;",
    "  float d = length(f - off);",
    "  float tw = 0.55 + 0.45 * sin(u_time * 1.6 + r * 42.0);",
    "  float s = on * smoothstep(0.30, 0.0, d) * tw;",
    "  return vec3(0.55, 0.82, 0.95) * s * 0.55;",
    "}",

    "mat3 setCamera(vec3 ro, vec3 ta){",
    "  vec3 cw = normalize(ta - ro);",
    "  vec3 cp = vec3(0.0, 1.0, 0.0);",
    "  vec3 cu = normalize(cross(cw, cp));",
    "  vec3 cv = cross(cu, cw);",
    "  return mat3(cu, cv, cw);",
    "}",

    "void main(){",
    "  vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;",
    "  uv -= u_shift;",

    "  vec3 ro = vec3(0.0, 0.62, 5.45);",
    "  ro.yz *= rot(-u_mouse.y * 0.26);",
    "  ro.xz *= rot(u_mouse.x * 0.50 + u_time * 0.05);",
    "  mat3 cam = setCamera(ro, vec3(0.0));",
    "  vec3 rd = cam * normalize(vec3(uv, 1.45));",

    "  vec3 col = starfield(rd);",
    "  vec3 glow = vec3(0.0);",
    "  float t = 0.0;",
    "  float id = 0.0;",
    "  float hit = 0.0;",
    "  vec3 pos = ro;",

    "  for (int i = 0; i < 96; i++){",
    "    pos = ro + rd * t;",
    "    vec2 h = map(pos);",
    "    float d = h.x;",
    "    float gw = (h.y < 1.5) ? 0.009 : 0.024;",
    "    glow += matColor(h.y) * exp(-abs(d) * 15.0) * gw;",
    "    if (d < 0.0018){ hit = 1.0; id = h.y; break; }",
    "    t += max(d * 0.62, 0.004);",
    "    if (t > 11.0) break;",
    "  }",

    "  if (hit > 0.5){",
    "    vec3 nor = calcNormal(pos);",
    "    float fres = pow(1.0 - clamp(dot(nor, -rd), 0.0, 1.0), 2.6);",
    "    if (id < 1.5){",
    "      float pulse = 0.5 + 0.5 * sin(u_time * 2.1);",
    "      vec3 pn = pos;",
    "      pn.xz *= rot(u_time * 0.16);",
    "      pn.xy *= rot(u_time * 0.11);",
    "      float v1 = vnoise(pn * 4.0 + vec3(0.0, u_time * 0.45, 0.0));",
    "      float v2 = vnoise(pn * 9.5 - vec3(u_time * 0.35, 0.0, 0.0));",
    "      float veins = pow(clamp(v1 * 1.15, 0.0, 1.0), 3.2) + 0.35 * pow(clamp(v2, 0.0, 1.0), 5.0);",
    "      vec3 base = vec3(0.010, 0.052, 0.058);",
    "      base += vec3(0.10, 0.88, 0.82) * veins * (0.55 + 0.45 * pulse);",
    "      base += vec3(0.50, 1.00, 0.97) * pow(fres, 1.9) * 1.10;",
    "      col += base;",
    "    } else {",
    "      col += matColor(id) * (0.30 + fres * 1.7);",
    "    }",
    "  }",

    "  col += glow;",

    /* Weiches Halo um den Kern herum */
    "  float halo = 1.0 / (1.0 + dot(uv, uv) * 26.0);",
    "  col += vec3(0.05, 0.26, 0.26) * halo * 0.55;",

    /* Breite Ansicht: Textspalte links abdunkeln. Schmale Ansicht: alles dimmen. */
    "  float sx = gl_FragCoord.x / u_res.x;",
    "  float wide = step(0.01, u_shift.x);",
    "  float sideDim = mix(0.22, 1.0, smoothstep(0.14, 0.58, sx));",
    "  col *= mix(0.30, sideDim, wide);",

    "  col *= u_fade;",
    "  col = col / (1.0 + col * 0.72);",
    "  col = pow(max(col, 0.0), vec3(0.4545));",

    "  float a = clamp(max(col.r, max(col.g, col.b)) * 1.55, 0.0, 1.0);",
    "  gl_FragColor = vec4(col, a);",
    "}"
  ].join("\n");

  /* ------------------------------------------------------------- Kompilieren */

  function compile(type, src) {
    var sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      if (window.console) console.warn("[core] shader:", gl.getShaderInfoLog(sh));
      gl.deleteShader(sh);
      return null;
    }
    return sh;
  }

  var vs = compile(gl.VERTEX_SHADER, VERT);
  var fs = compile(gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) { useFallback(); return; }

  var prog = gl.createProgram();
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    if (window.console) console.warn("[core] link:", gl.getProgramInfoLog(prog));
    useFallback();
    return;
  }
  gl.useProgram(prog);

  var buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  var loc = gl.getAttribLocation(prog, "a_pos");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

  var uRes = gl.getUniformLocation(prog, "u_res");
  var uTime = gl.getUniformLocation(prog, "u_time");
  var uMouse = gl.getUniformLocation(prog, "u_mouse");
  var uShift = gl.getUniformLocation(prog, "u_shift");
  var uFade = gl.getUniformLocation(prog, "u_fade");

  /* ------------------------------------------------------------------ State */

  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var scale = Math.min(window.devicePixelRatio || 1, 1.5);
  var mouse = { x: 0, y: 0 };
  var target = { x: 0, y: 0 };
  var fade = 1;
  var visible = true;
  var running = false;
  var start = performance.now();
  var samples = 0;
  var accum = 0;
  var downgrades = 0;

  function resize() {
    var r = canvas.getBoundingClientRect();
    var w = Math.max(1, Math.round(r.width * scale));
    var h = Math.max(1, Math.round(r.height * scale));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
      gl.viewport(0, 0, w, h);
    }
    return r;
  }

  /* Auf breiten Layouts sitzt der Kern rechts neben der Textspalte,
     auf schmalen zentriert er sich als gedämpfter Hintergrund. */
  function shiftFor(width) {
    if (width < 1000) return [0.0, 0.0];
    return [0.36, 0.02];
  }

  function frame(now) {
    if (!running) return;
    var t0 = performance.now();
    var rect = resize();

    mouse.x += (target.x - mouse.x) * 0.055;
    mouse.y += (target.y - mouse.y) * 0.055;

    gl.uniform2f(uRes, canvas.width, canvas.height);
    gl.uniform1f(uTime, reduced ? 12.0 : (now - start) / 1000);
    gl.uniform2f(uMouse, mouse.x, mouse.y);
    var sh = shiftFor(rect.width);
    gl.uniform2f(uShift, sh[0], sh[1]);
    gl.uniform1f(uFade, fade);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

    canvas.classList.add("is-ready");

    if (reduced) { running = false; return; }

    /* Adaptive Auflösung: bei schwacher GPU herunterskalieren */
    if (downgrades < 2) {
      accum += performance.now() - t0;
      samples++;
      if (samples >= 45) {
        if (accum / samples > 20) {
          scale = Math.max(0.6, scale * 0.7);
          downgrades++;
        }
        samples = 0;
        accum = 0;
      }
    }
    requestAnimationFrame(frame);
  }

  function play() {
    if (running) return;
    running = true;
    requestAnimationFrame(frame);
  }
  function pause() { running = false; }

  /* ------------------------------------------------------------------ Events */

  window.addEventListener("resize", function () { resize(); if (!running) play(); }, { passive: true });

  window.addEventListener("mousemove", function (e) {
    target.x = (e.clientX / window.innerWidth) * 2 - 1;
    target.y = (e.clientY / window.innerHeight) * 2 - 1;
  }, { passive: true });

  window.addEventListener("deviceorientation", function (e) {
    if (e.gamma == null || e.beta == null) return;
    target.x = Math.max(-1, Math.min(1, e.gamma / 35));
    target.y = Math.max(-1, Math.min(1, (e.beta - 45) / 40));
  }, { passive: true });

  /* Ausblenden beim Scrollen — spart Rechenzeit und hält den Text lesbar */
  window.addEventListener("scroll", function () {
    var h = window.innerHeight || 1;
    fade = Math.max(0, 1 - (window.scrollY / (h * 0.85)));
  }, { passive: true });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) pause();
    else if (visible) play();
  });

  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      if (visible && !document.hidden) play();
      else pause();
    }, { threshold: 0 });
    io.observe(canvas);
  }

  gl.clearColor(0, 0, 0, 0);
  play();

  /* Kontextverlust sauber abfangen */
  canvas.addEventListener("webglcontextlost", function (e) { e.preventDefault(); pause(); useFallback(); });
})();
