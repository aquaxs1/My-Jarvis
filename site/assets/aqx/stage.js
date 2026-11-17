/*
 * The 3D stage every AquAxs project site shares.
 *
 * A scene file (assets/scene.js) exports one function. It receives the stage
 * and builds its objects into `stage.frame`; the stage takes care of the rest:
 * renderer, camera, lights, resizing, where the subject sits (right of the
 * copy on a wide screen, above it on a phone), pointer and touch, pausing
 * when the canvas is off screen, and standing still for reduced motion.
 *
 *   export default function (stage) {
 *     const { THREE, frame } = stage;
 *     const cube = new THREE.Mesh(new THREE.BoxGeometry(), stage.mat.solid(stage.accent));
 *     frame.add(cube);
 *     stage.onFrame((time, delta) => { cube.rotation.y = time * 0.4 + stage.yaw; });
 *   }
 */

import * as THREE from './three.module.min.js';

export { THREE };

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export function createStage(holder, options = {}) {
  const canvas = holder.querySelector('canvas');
  if (!canvas) return null;

  const mode = options.mode || 'hero';
  const reduced = Boolean(options.reduced);
  const coarse = window.matchMedia('(pointer: coarse)').matches;

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
  } catch {
    holder.dataset.ready = 'false';
    return null;
  }

  let pixelRatio = Math.min(window.devicePixelRatio || 1, coarse ? 1.75 : 2);
  renderer.setPixelRatio(pixelRatio);
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 120);
  camera.position.set(0, 0, 10);

  // Everything a scene builds goes in here; the stage places and scales it.
  const frame = new THREE.Group();
  scene.add(frame);

  const style = getComputedStyle(document.documentElement);
  const token = (name, fallback) => new THREE.Color(style.getPropertyValue(name).trim() || fallback);
  const accent = token('--ac', '#3be07a');
  const accentHi = token('--ac-hi', '#7df0a8');
  const gold = token('--gold', '#c9a227');

  const frameFns = [];
  const resizeFns = [];
  const clickFns = [];

  const stage = {
    THREE,
    renderer,
    scene,
    camera,
    frame,
    holder,
    mode,
    reduced,
    coarse,
    accent,
    accentHi,
    gold,
    white: new THREE.Color('#f2f5f2'),
    /** Smoothed pointer over the stage, -1 … 1 on both axes. */
    pointer: { x: 0, y: 0, inside: false },
    /** Rotation to add to the subject: pointer parallax plus what was dragged. */
    yaw: 0,
    pitch: 0,
    /** 0 at the top of the page, 1 once the stage has scrolled away. */
    scroll: 0,
    /** Seconds the scene has been running. */
    time: 0,
    /** Visible width and height of the scene at the subject's depth. */
    view: { width: 1, height: 1, wide: true },
    /** A hint for heavy scenes: 1 on a desktop, lower on phones. */
    quality: coarse ? 0.6 : 1,
    onFrame: (fn) => frameFns.push(fn),
    onResize: (fn) => resizeFns.push(fn),
    /** fn(ndc: THREE.Vector2, raycaster) — a tap or click that was not a drag. */
    onClick: (fn) => clickFns.push(fn),
  };

  /* -------------------------------------------------------------- lights */

  /** A studio: soft sky, a key light, and a rim in the project's accent. */
  stage.lights = ({ ambient = 0.55, key = 2.4, rim = 26 } = {}) => {
    const hemi = new THREE.HemisphereLight(0xffffff, 0x0a120d, ambient);
    const keyLight = new THREE.DirectionalLight(0xffffff, key);
    keyLight.position.set(-4, 6, 7);
    const rimLight = new THREE.PointLight(accent, rim, 30, 1.6);
    rimLight.position.set(5, -2, -4);
    const fill = new THREE.PointLight(accentHi, rim * 0.45, 30, 1.6);
    fill.position.set(-6, -3, 4);
    scene.add(hemi, keyLight, rimLight, fill);
    return { hemi, keyLight, rimLight, fill };
  };

  /** Reflections for metal and glass: three soft boxes in a dark room. */
  stage.environment = (strength = 1) => {
    const room = new THREE.Scene();
    const box = (color, intensity, w, h, x, y, z) => {
      const mesh = new THREE.Mesh(
        new THREE.PlaneGeometry(w, h),
        new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(intensity), side: THREE.DoubleSide }),
      );
      mesh.position.set(x, y, z);
      mesh.lookAt(0, 0, 0);
      room.add(mesh);
    };
    room.background = new THREE.Color(0x020403);
    box(0xffffff, 3.2 * strength, 8, 5, 0, 8, 2);
    box(accentHi, 2.4 * strength, 5, 8, 9, 1, 2);
    box(gold, 1.3 * strength, 5, 6, -9, -1, -3);
    box(0xffffff, 0.9 * strength, 10, 3, 0, -2, 10);
    const pmrem = new THREE.PMREMGenerator(renderer);
    const texture = pmrem.fromScene(room, 0.03).texture;
    scene.environment = texture;
    pmrem.dispose();
    room.traverse((object) => {
      if (object.geometry) object.geometry.dispose();
      if (object.material) object.material.dispose();
    });
    return texture;
  };

  /* ----------------------------------------------------------- materials */

  stage.mat = {
    solid: (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.5, metalness: 0.08, ...extra }),
    metal: (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.24, metalness: 1, ...extra }),
    glass: (color, extra = {}) =>
      new THREE.MeshPhysicalMaterial({
        color,
        roughness: 0.12,
        metalness: 0,
        clearcoat: 1,
        clearcoatRoughness: 0.12,
        transparent: true,
        opacity: 0.3,
        side: THREE.DoubleSide,
        depthWrite: false,
        ...extra,
      }),
    glow: (color, extra = {}) =>
      new THREE.MeshBasicMaterial({ color, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, ...extra }),
    line: (color, opacity = 0.6) => new THREE.LineBasicMaterial({ color, transparent: true, opacity }),
  };

  /* ------------------------------------------------------------- helpers */

  /** The outline of a geometry, as thin lines. */
  stage.edges = (geometry, color = accentHi, opacity = 0.6, angle = 20) =>
    new THREE.LineSegments(new THREE.EdgesGeometry(geometry, angle), stage.mat.line(color, opacity));

  /** A box with rounded corners (a card, a window, a slab of glass). */
  stage.slab = (width, height, depth, radius = 0.12) => {
    const r = Math.min(radius, width / 2, height / 2);
    const w = width / 2;
    const h = height / 2;
    const shape = new THREE.Shape();
    shape.moveTo(-w + r, -h);
    shape.lineTo(w - r, -h);
    shape.quadraticCurveTo(w, -h, w, -h + r);
    shape.lineTo(w, h - r);
    shape.quadraticCurveTo(w, h, w - r, h);
    shape.lineTo(-w + r, h);
    shape.quadraticCurveTo(-w, h, -w, h - r);
    shape.lineTo(-w, -h + r);
    shape.quadraticCurveTo(-w, -h, -w + r, -h);
    const bevel = Math.min(depth * 0.3, r * 0.5, 0.04);
    const geometry = new THREE.ExtrudeGeometry(shape, {
      depth: Math.max(0.001, depth - bevel * 2),
      bevelEnabled: bevel > 0,
      bevelThickness: bevel,
      bevelSize: bevel,
      bevelSegments: 3,
      curveSegments: 10,
    });
    geometry.center();
    return geometry;
  };

  let glowTexture;
  /** A soft round sprite texture: white in the middle, nothing at the edge. */
  stage.glowTexture = () => {
    if (glowTexture) return glowTexture;
    const size = 128;
    const c = document.createElement('canvas');
    c.width = c.height = size;
    const g = c.getContext('2d');
    const gradient = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    gradient.addColorStop(0, 'rgba(255,255,255,1)');
    gradient.addColorStop(0.25, 'rgba(255,255,255,0.55)');
    gradient.addColorStop(0.6, 'rgba(255,255,255,0.12)');
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = gradient;
    g.fillRect(0, 0, size, size);
    glowTexture = new THREE.CanvasTexture(c);
    glowTexture.colorSpace = THREE.SRGBColorSpace;
    return glowTexture;
  };

  /** A halo that always faces the camera. */
  stage.halo = (color = accent, size = 4, opacity = 0.55) => {
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: stage.glowTexture(),
        color,
        transparent: true,
        opacity,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        depthTest: false,
      }),
    );
    sprite.scale.setScalar(size);
    sprite.renderOrder = -1;
    return sprite;
  };

  /** Fine dust around the subject: it gives the empty space some depth. */
  stage.dust = ({ count = 260, radius = 7, size = 0.045, color = stage.white, opacity = 0.7 } = {}) => {
    const n = Math.round(count * stage.quality);
    const positions = new Float32Array(n * 3);
    for (let i = 0; i < n; i += 1) {
      const r = radius * (0.35 + Math.random() * 0.65);
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.cos(phi) * 0.7;
      positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const points = new THREE.Points(
      geometry,
      new THREE.PointsMaterial({
        map: stage.glowTexture(),
        color,
        size,
        transparent: true,
        opacity,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        sizeAttenuation: true,
      }),
    );
    frame.add(points);
    stage.onFrame((time) => {
      points.rotation.y = time * 0.03 + stage.yaw * 0.35;
      points.rotation.x = stage.pitch * 0.35;
    });
    return points;
  };

  /**
   * Text or drawing on a flat plane. `paint(ctx, w, h)` draws on a canvas;
   * the texture refreshes once the page's fonts have loaded.
   */
  stage.plate = (width, height, paint, { resolution = 256, transparent = true } = {}) => {
    const c = document.createElement('canvas');
    c.width = Math.round(width * resolution);
    c.height = Math.round(height * resolution);
    const g = c.getContext('2d');
    const texture = new THREE.CanvasTexture(c);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    const redraw = () => {
      g.clearRect(0, 0, c.width, c.height);
      paint(g, c.width, c.height);
      texture.needsUpdate = true;
    };
    redraw();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(redraw);
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(width, height),
      new THREE.MeshBasicMaterial({ map: texture, transparent, depthWrite: !transparent }),
    );
    mesh.userData.redraw = redraw;
    return mesh;
  };

  /** The page's fonts, for drawing on plates. */
  stage.font = {
    display: (px, weight = 600, italic = false) => `${italic ? 'italic ' : ''}${weight} ${px}px "Bodoni Moda", "Times New Roman", serif`,
    body: (px, weight = 500) => `${weight} ${px}px "Inter Tight", system-ui, sans-serif`,
    mono: (px, weight = 500) => `${weight} ${px}px "JetBrains Mono", ui-monospace, monospace`,
  };

  /* -------------------------------------------------------------- layout */

  const layout = () => {
    const width = Math.max(1, holder.clientWidth);
    const height = Math.max(1, holder.clientHeight);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();

    const viewHeight = 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * camera.position.z;
    const viewWidth = viewHeight * camera.aspect;
    const wide = mode === 'hero' ? width >= 860 && camera.aspect > 1.15 : true;
    stage.view.width = viewWidth;
    stage.view.height = viewHeight;
    stage.view.wide = wide;

    if (mode === 'hero') {
      if (wide) {
        // Right of the copy, never further out than the page's content column.
        const span = Math.min(viewWidth, viewHeight * 2.3);
        frame.position.set(span * 0.235, 0.05, 0);
        frame.scale.setScalar(clamp(Math.min(viewWidth * 0.36, viewHeight * 0.74) / 4.4, 0.6, 1.08));
      } else {
        // Above the copy, in the upper third of a tall phone hero.
        frame.position.set(0, viewHeight * 0.245, 0);
        frame.scale.setScalar(clamp(Math.min(viewWidth * 0.86, viewHeight * 0.4) / 4.4, 0.4, 0.95));
      }
    } else {
      frame.position.set(0, 0, 0);
      frame.scale.setScalar(clamp(Math.min(viewWidth, viewHeight) / 5.2, 0.4, 1.1));
    }
    for (const fn of resizeFns) fn(stage.view);
    if (!running) renderOnce();
  };

  /* ------------------------------------------------------------- pointer */

  const target = { x: 0, y: 0 };
  const drag = { active: false, id: null, x: 0, y: 0, startX: 0, startY: 0, moved: 0, yaw: 0, pitch: 0, vyaw: 0, vpitch: 0 };
  const raycaster = new THREE.Raycaster();

  const local = (event) => {
    const rect = holder.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * 2 - 1,
      y: -(((event.clientY - rect.top) / rect.height) * 2 - 1),
      inside: event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom,
    };
  };

  if (!reduced) {
    window.addEventListener(
      'pointermove',
      (event) => {
        const p = local(event);
        stage.pointer.inside = p.inside;
        if (event.pointerType === 'mouse' || drag.active) {
          target.x = clamp(p.x, -1.2, 1.2);
          target.y = clamp(p.y, -1.2, 1.2);
        }
        if (drag.active && event.pointerId === drag.id) {
          const dx = event.clientX - drag.x;
          const dy = event.clientY - drag.y;
          drag.x = event.clientX;
          drag.y = event.clientY;
          drag.moved += Math.abs(dx) + Math.abs(dy);
          drag.vyaw = dx * 0.006;
          drag.vpitch = dy * 0.004;
          drag.yaw += drag.vyaw;
          drag.pitch = clamp(drag.pitch + drag.vpitch, -0.7, 0.7);
        }
      },
      { passive: true },
    );

    holder.addEventListener('pointerdown', (event) => {
      drag.active = true;
      drag.id = event.pointerId;
      drag.x = drag.startX = event.clientX;
      drag.y = drag.startY = event.clientY;
      drag.moved = 0;
      holder.style.cursor = 'grabbing';
    });

    const release = (event) => {
      if (!drag.active || event.pointerId !== drag.id) return;
      drag.active = false;
      holder.style.cursor = '';
      if (drag.moved < 8 && event.type === 'pointerup') {
        const p = local(event);
        const ndc = new THREE.Vector2(p.x, p.y);
        raycaster.setFromCamera(ndc, camera);
        for (const fn of clickFns) fn(ndc, raycaster);
      }
    };
    window.addEventListener('pointerup', release, { passive: true });
    window.addEventListener('pointercancel', release, { passive: true });
  }

  const onScroll = () => {
    const rect = holder.getBoundingClientRect();
    stage.scroll = clamp(-rect.top / Math.max(1, rect.height), 0, 1);
  };
  window.addEventListener('scroll', onScroll, { passive: true });

  /* ---------------------------------------------------------------- loop */

  let running = false;
  let visible = true;
  let raf = 0;
  let last = 0;
  let slowFrames = 0;
  let frames = 0;

  const step = (delta) => {
    stage.time += delta;
    stage.pointer.x += (target.x - stage.pointer.x) * Math.min(1, delta * 4);
    stage.pointer.y += (target.y - stage.pointer.y) * Math.min(1, delta * 4);
    if (!drag.active) {
      // What was thrown keeps turning, then eases back towards the front.
      drag.yaw += drag.vyaw;
      drag.vyaw *= Math.pow(0.04, delta);
      drag.yaw *= Math.pow(0.82, delta);
      drag.pitch *= Math.pow(0.25, delta);
    }
    stage.yaw = stage.pointer.x * 0.32 + drag.yaw;
    stage.pitch = -stage.pointer.y * 0.2 + drag.pitch;
    for (const fn of frameFns) fn(stage.time, delta);
    renderer.render(scene, camera);
  };

  const renderOnce = () => {
    for (const fn of frameFns) fn(stage.time, 0);
    renderer.render(scene, camera);
  };

  const tick = (now) => {
    if (!running) return;
    raf = requestAnimationFrame(tick);
    const ms = now - last;
    last = now;
    // The first frame's timestamp can lie before the moment play() was called.
    const delta = Math.max(0, Math.min(0.05, ms / 1000));

    // A device that cannot keep up gets fewer pixels rather than a slideshow.
    frames += 1;
    if (frames > 20 && frames < 200) {
      if (ms > 30) slowFrames += 1;
      if (slowFrames > 24 && pixelRatio > 1) {
        pixelRatio = Math.max(1, pixelRatio - 0.5);
        renderer.setPixelRatio(pixelRatio);
        layout();
        slowFrames = 0;
        frames = 0;
      }
    }
    step(delta);
  };

  const play = () => {
    if (running || reduced || !visible || document.hidden) return;
    running = true;
    last = performance.now();
    raf = requestAnimationFrame(tick);
  };

  const pause = () => {
    running = false;
    cancelAnimationFrame(raf);
  };

  stage.start = () => {
    layout();
    onScroll();
    if (reduced) {
      // One still frame, a moment into the animation so it is not the empty start.
      stage.time = 2.4;
      for (let i = 0; i < 90; i += 1) for (const fn of frameFns) fn(stage.time + i / 60, 1 / 60);
      stage.time += 1.5;
      renderOnce();
    } else {
      step(1 / 60);
    }
    holder.dataset.ready = 'true';

    if ('ResizeObserver' in window) new ResizeObserver(layout).observe(holder);
    else window.addEventListener('resize', layout);

    if ('IntersectionObserver' in window) {
      new IntersectionObserver((entries) => {
        visible = entries[0].isIntersecting;
        if (visible) play();
        else pause();
      }).observe(holder);
    }
    document.addEventListener('visibilitychange', () => (document.hidden ? pause() : play()));
    play();
  };

  renderer.domElement.addEventListener('webglcontextlost', (event) => {
    event.preventDefault();
    pause();
    holder.dataset.ready = 'false';
  });

  return stage;
}
