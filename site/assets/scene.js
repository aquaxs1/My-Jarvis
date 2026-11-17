/*
 * My Jarvis — the core, listening.
 *
 * A living core inside three rings. Click or tap — or wait a moment — and it
 * runs through one exchange from the demo on this page: it listens (teal,
 * a voice wave around it), thinks (amber) and answers. Teal and amber are the
 * colours of the status orb in the app itself.
 */
export default function scene(stage) {
  const { THREE, frame } = stage;
  stage.lights({ ambient: 0.55, key: 2.4, rim: 32 });
  stage.environment(1);

  const root = new THREE.Group();
  root.scale.setScalar(0.92);
  frame.add(root);

  const TEAL = new THREE.Color('#3fe0cf');
  const AMBER = new THREE.Color('#ffb347');
  const halo = stage.halo(TEAL, 7.5, 0.3);
  root.add(halo);

  /* --------------------------------------------------------------- core */

  // A sphere whose surface moves: slow breathing, and ripples while it hears a voice.
  const uniforms = { uTime: { value: 0 }, uVoice: { value: 0 } };
  // Flat shading takes its normals from the moved surface, so the ripples catch the light.
  const coreMaterial = new THREE.MeshStandardMaterial({
    color: 0x05211f,
    emissive: TEAL.clone(),
    emissiveIntensity: 0.22,
    roughness: 0.18,
    metalness: 0.65,
    flatShading: true,
  });
  coreMaterial.onBeforeCompile = (shader) => {
    shader.uniforms.uTime = uniforms.uTime;
    shader.uniforms.uVoice = uniforms.uVoice;
    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        `#include <common>
        uniform float uTime;
        uniform float uVoice;
        float wobble(vec3 p) {
          return sin(p.x * 3.1 + uTime * 1.3) * sin(p.y * 2.7 - uTime * 1.1) * sin(p.z * 3.3 + uTime * 0.9);
        }`,
      )
      .replace(
        '#include <begin_vertex>',
        `#include <begin_vertex>
        float ripple = sin(position.y * 9.0 - uTime * 9.0) * sin(position.x * 7.0 + uTime * 6.0);
        transformed += normal * (wobble(position) * 0.06 + ripple * 0.07 * uVoice);`,
      );
  };
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 24), coreMaterial);
  root.add(core);

  // A glow that sits on the rim of the core, strongest where you look past it.
  const rimMaterial = new THREE.ShaderMaterial({
    uniforms: { uColor: { value: TEAL.clone() }, uPower: { value: 0.9 } },
    vertexShader: `
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        vec4 world = modelViewMatrix * vec4(position, 1.0);
        vNormal = normalize(normalMatrix * normal);
        vView = normalize(-world.xyz);
        gl_Position = projectionMatrix * world;
      }`,
    fragmentShader: `
      uniform vec3 uColor;
      uniform float uPower;
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        float rim = pow(1.0 - abs(dot(vNormal, vView)), 2.6);
        gl_FragColor = vec4(uColor * rim * 1.6, rim * uPower);
      }`,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const shell = new THREE.Mesh(new THREE.SphereGeometry(1.16, 64, 48), rimMaterial);
  root.add(shell);

  // A fine geodesic cage around it, turning the other way.
  const cage = stage.edges(new THREE.IcosahedronGeometry(1.3, 2), stage.accentHi, 0.16, 1);
  root.add(cage);

  /* -------------------------------------------------------------- rings */

  const ring = (radius, tube, color, tilt) => {
    const group = new THREE.Group();
    group.rotation.set(tilt[0], tilt[1], tilt[2]);
    const mesh = new THREE.Mesh(new THREE.TorusGeometry(radius, tube, 12, 180), stage.mat.glow(color, { opacity: 0.85 }));
    group.add(mesh);
    root.add(group);
    return { group, mesh };
  };
  const rings = [
    ring(1.75, 0.012, TEAL, [Math.PI / 2.3, 0, 0.2]),
    ring(2.05, 0.008, TEAL, [Math.PI / 1.8, 0.5, -0.4]),
    ring(1.95, 0.01, stage.gold, [1.15, 0.55, 0.35]),
  ];

  // Beads on the gold ring, like the marks on a dial.
  const beads = new THREE.InstancedMesh(new THREE.SphereGeometry(0.035, 10, 8), stage.mat.glow(stage.gold, { opacity: 1 }), 12);
  const dummy = new THREE.Object3D();
  for (let i = 0; i < 12; i += 1) {
    const a = (i / 12) * Math.PI * 2;
    dummy.position.set(Math.cos(a) * 1.95, Math.sin(a) * 1.95, 0);
    dummy.scale.setScalar(i % 3 === 0 ? 1.6 : 0.9);
    dummy.updateMatrix();
    beads.setMatrixAt(i, dummy.matrix);
  }
  rings[2].group.add(beads);

  /* ------------------------------------------------------------ voice wave */

  const POINTS = 160;
  const wave = new THREE.LineLoop(new THREE.BufferGeometry(), stage.mat.line(stage.accentHi, 0));
  wave.geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(POINTS * 3), 3));
  root.add(wave);

  stage.dust({ count: 220, radius: 6.5, color: stage.accentHi, opacity: 0.5 });

  /* --------------------------------------------------------------- words */

  // The exchanges from the terminal demo on this page.
  const TALK = [
    { you: 'Look at my screen', jarvis: 'A merge conflict in core/brain.py, line 214.' },
    { you: 'Remember that I prefer Python', jarvis: 'Stored, encrypted, in ~/.jarvis/memory.' },
    { you: 'Give me my briefing', jarvis: '18°C, 3 events, 2 open tasks, 5 unread emails.' },
  ];

  let label = '';
  const plate = stage.plate(4.6, 0.36, (g, w, h) => {
    g.fillStyle = '#f2f5f2';
    let size = Math.round(h * 0.42);
    g.font = stage.font.mono(size);
    while (size > 8 && g.measureText(label).width > w * 0.96) {
      size -= 1;
      g.font = stage.font.mono(size);
    }
    g.textBaseline = 'middle';
    g.textAlign = 'center';
    g.fillText(label, w / 2, h / 2);
  });
  plate.position.set(0, -2.45, 1.2);
  frame.add(plate);
  const fit = (view) => {
    plate.visible = stage.mode === 'hero' && view.wide;
  };
  stage.onResize(fit);
  fit(stage.view);
  const say = (text) => {
    if (text === label) return;
    label = text;
    plate.userData.redraw();
  };
  say('Ready');

  /* ----------------------------------------------------------- behaviour */

  // idle → listening (2.4 s) → thinking (1.1 s) → answering (3.2 s) → idle
  let state = 'idle';
  let clock = 0;
  let turn = -1;
  let voice = 0;
  let warmth = 0;
  const colour = new THREE.Color();

  const begin = () => {
    if (state !== 'idle') return;
    turn = (turn + 1) % TALK.length;
    state = 'listening';
    clock = 0;
  };
  stage.onClick(begin);

  stage.onFrame((time, delta) => {
    clock += delta;
    uniforms.uTime.value = time;

    if (state === 'idle' && clock > 4.5) begin();
    if (state === 'listening') {
      const words = TALK[turn].you;
      const shown = Math.min(words.length, Math.floor((clock / 1.8) * words.length));
      say(`“${words.slice(0, shown)}${shown < words.length ? '' : '”'}`);
      if (clock > 2.4) {
        state = 'thinking';
        clock = 0;
        say('Thinking…');
      }
    } else if (state === 'thinking' && clock > 1.1) {
      state = 'answering';
      clock = 0;
      say(TALK[turn].jarvis);
    } else if (state === 'answering' && clock > 3.2) {
      state = 'idle';
      clock = 0;
      say('Ready');
    }

    // A made-up voice envelope while it listens: syllables rising and falling.
    const target = state === 'listening' ? 0.45 + Math.abs(Math.sin(clock * 11)) * 0.55 * Math.abs(Math.sin(clock * 2.3 + 1)) : 0;
    voice += (target - voice) * Math.min(1, delta * 10);
    uniforms.uVoice.value = voice;
    warmth += ((state === 'thinking' ? 1 : 0) - warmth) * Math.min(1, delta * 4);

    colour.copy(TEAL).lerp(AMBER, warmth);
    coreMaterial.emissive.copy(colour);
    coreMaterial.emissiveIntensity = 0.2 + voice * 0.5 + warmth * 0.3 + (state === 'answering' ? 0.12 : 0);
    rimMaterial.uniforms.uColor.value.copy(colour);
    rimMaterial.uniforms.uPower.value = 0.85 + voice * 0.6;
    cage.rotation.y = -time * 0.08;
    cage.rotation.x = time * 0.05;
    cage.material.color.copy(colour).lerp(stage.white, 0.4);
    halo.material.color.copy(colour);
    halo.material.opacity = 0.26 + voice * 0.2 + warmth * 0.1;
    rings[0].mesh.material.color.copy(colour);
    rings[1].mesh.material.color.copy(colour);

    const speed = 1 + voice * 2.5 + warmth * 4;
    rings[0].group.rotation.z += delta * 0.35 * speed;
    rings[1].group.rotation.z -= delta * 0.25 * speed;
    rings[2].group.rotation.z += delta * 0.18 * speed;

    core.rotation.y = time * 0.15 + stage.yaw;
    core.rotation.x = stage.pitch;
    root.rotation.y = stage.yaw * 0.4;
    root.rotation.x = stage.pitch * 0.6;
    root.position.y = 0.1 + Math.sin(time * 0.7) * 0.05;

    // The voice wave: a ring around the core that only shows while it listens.
    const position = wave.geometry.attributes.position;
    for (let i = 0; i < POINTS; i += 1) {
      const a = (i / POINTS) * Math.PI * 2;
      const r = 1.42 + voice * 0.16 * Math.sin(a * 9 + time * 12) * Math.sin(a * 4 - time * 7);
      position.setXYZ(i, Math.cos(a) * r, Math.sin(a) * r, 0.25);
    }
    position.needsUpdate = true;
    wave.material.opacity = Math.min(1, voice * 1.6);
  });
}
