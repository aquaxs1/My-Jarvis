/*
 * AQX — the shared behaviour of the AquAxs project sites.
 *
 * Everything here is optional polish: with scripts off the pages still read
 * top to bottom. Nothing is loaded from another host, nothing is stored.
 *
 *   sky        slow white dots behind the page, as on aqua.is-a.dev
 *   nav        the bar tightens on scroll, the three lines open the menu
 *   reveal     sections fade in once, on first sight
 *   tilt       glass cards lean towards the pointer, the highlight follows it
 *   magnet     the main button drifts a little towards the pointer
 *   typed      the line under the headline types itself
 *   count      numbers count up when they come into view
 *   copy       a copy button on every code block
 *   showcase   panels straighten as they scroll in
 *   tabs       a row of tabs with one panel on show
 *   coverflow  a row of pictures that turn in space as you swipe
 *   stage      loads the page's 3D scene (stage.js + three.js) when it is needed
 */

const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const mouse = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
const root = document.documentElement;

/* -------------------------------------------------------------------- sky */

function sky() {
  const canvas = document.querySelector('.aqx-sky');
  const ctx = canvas && canvas.getContext('2d');
  if (!ctx) return;

  let dots = [];
  let frame = 0;
  let width = 0;
  let height = 0;
  let px = 0;
  let py = 0;
  let tx = 0;
  let ty = 0;

  const resize = () => {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

    const count = Math.min(110, Math.floor(width / 12));
    dots = Array.from({ length: count }, () => ({
      x: Math.random() * width,
      y: Math.random() * height,
      r: Math.random() * 1.25 + 0.25,
      vx: (Math.random() - 0.5) * 0.12,
      vy: (Math.random() - 0.5) * 0.12,
      a: Math.random() * 0.45 + 0.12,
      // Depth: near dots answer the pointer and the scroll more than far ones.
      z: Math.random() * 0.9 + 0.1,
    }));
    if (reduced) draw();
  };

  const draw = () => {
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#fff';
    px += (tx - px) * 0.04;
    py += (ty - py) * 0.04;
    const scroll = window.scrollY;

    for (const d of dots) {
      if (!reduced) {
        d.x += d.vx;
        d.y += d.vy;
        if (d.x < 0) d.x = width;
        if (d.x > width) d.x = 0;
        if (d.y < 0) d.y = height;
        if (d.y > height) d.y = 0;
      }
      let y = (d.y - scroll * d.z * 0.18) % height;
      if (y < 0) y += height;
      const x = d.x + px * d.z * 26;
      ctx.globalAlpha = d.a;
      ctx.beginPath();
      ctx.arc(x, y + py * d.z * 18, d.r, 0, Math.PI * 2);
      ctx.fill();
    }
  };

  const loop = () => {
    draw();
    frame = requestAnimationFrame(loop);
  };

  window.addEventListener('resize', resize);
  if (mouse && !reduced) {
    window.addEventListener(
      'pointermove',
      (event) => {
        tx = event.clientX / width - 0.5;
        ty = event.clientY / height - 0.5;
      },
      { passive: true },
    );
  }
  document.addEventListener('visibilitychange', () => {
    cancelAnimationFrame(frame);
    if (!document.hidden && !reduced) loop();
  });
  resize();
  if (!reduced) loop();
}

/* -------------------------------------------------------------------- nav */

function nav() {
  const bar = document.querySelector('.nav');
  const progress = document.querySelector('.aqx-progress');

  let ticking = false;
  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      const y = window.scrollY;
      if (bar) bar.dataset.stuck = y > 24 ? 'true' : 'false';
      if (progress) {
        const max = root.scrollHeight - window.innerHeight;
        progress.style.setProperty('--scroll', max > 0 ? Math.min(1, y / max).toFixed(4) : '0');
      }
    });
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  if (!bar) return;
  const toggle = bar.querySelector('.nav-toggle');
  if (!toggle) return;

  const set = (open) => {
    bar.dataset.open = open ? 'true' : 'false';
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    root.style.overflow = open ? 'hidden' : '';
  };
  toggle.addEventListener('click', () => set(bar.dataset.open !== 'true'));
  bar.querySelectorAll('.nav-links a').forEach((link) => link.addEventListener('click', () => set(false)));
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') set(false);
  });
  window.matchMedia('(min-width: 861px)').addEventListener('change', () => set(false));
}

/* ----------------------------------------------------------------- reveal */

function reveal() {
  const items = [...document.querySelectorAll('.reveal')];
  if (!items.length) return;
  if (reduced || !('IntersectionObserver' in window)) {
    items.forEach((el) => el.classList.add('in'));
    return;
  }

  // Siblings arrive one after another rather than all at once.
  for (const el of items) {
    const siblings = [...el.parentElement.children].filter((child) => child.classList.contains('reveal'));
    const index = siblings.indexOf(el);
    if (index > 0) el.style.setProperty('--reveal-delay', `${Math.min(index, 6) * 70}ms`);
  }

  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add('in');
        observer.unobserve(entry.target);
      }
    },
    { rootMargin: '0px 0px -6% 0px', threshold: 0.08 },
  );
  items.forEach((el) => observer.observe(el));
}

/* ------------------------------------------------------------------- tilt */

function tilt() {
  if (!mouse || reduced) return;
  const MAX = 5;

  document.addEventListener(
    'pointermove',
    (event) => {
      const card = event.target.closest && event.target.closest('.card');
      if (!card || event.pointerType !== 'mouse') return;
      const rect = card.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      // Big cards lean less, or the far edge travels too far.
      const max = Math.min(MAX, 900 / Math.max(rect.width, rect.height));
      card.style.setProperty('--ry', `${(x * max * 2).toFixed(2)}deg`);
      card.style.setProperty('--rx', `${(-y * max * 2).toFixed(2)}deg`);
      card.style.setProperty('--gx', `${((x + 0.5) * 100).toFixed(1)}%`);
      card.style.setProperty('--gy', `${((y + 0.5) * 100).toFixed(1)}%`);
    },
    { passive: true },
  );

  document.addEventListener(
    'pointerout',
    (event) => {
      const card = event.target.closest && event.target.closest('.card');
      if (!card || card.contains(event.relatedTarget)) return;
      for (const name of ['--rx', '--ry', '--gx', '--gy']) card.style.removeProperty(name);
    },
    { passive: true },
  );
}

/* ----------------------------------------------------------------- magnet */

function magnet() {
  if (!mouse || reduced) return;
  for (const button of document.querySelectorAll('.btn-primary')) {
    button.addEventListener('pointermove', (event) => {
      const rect = button.getBoundingClientRect();
      const x = event.clientX - rect.left - rect.width / 2;
      const y = event.clientY - rect.top - rect.height / 2;
      button.style.setProperty('--tx', `${(x * 0.18).toFixed(1)}px`);
      button.style.setProperty('--ty', `${(y * 0.28).toFixed(1)}px`);
    });
    button.addEventListener('pointerleave', () => {
      button.style.removeProperty('--tx');
      button.style.removeProperty('--ty');
    });
  }
}

/* ------------------------------------------------------------------ typed */

function typed() {
  for (const el of document.querySelectorAll('[data-typed]')) {
    let lines;
    try {
      lines = JSON.parse(el.dataset.typed);
    } catch {
      continue;
    }
    if (!Array.isArray(lines) || !lines.length) continue;
    if (reduced) {
      el.textContent = lines[0];
      continue;
    }

    let line = 0;
    let at = 0;
    let deleting = false;
    el.textContent = '';
    el.setAttribute('aria-label', lines[0]);

    const step = () => {
      const text = lines[line];
      at += deleting ? -1 : 1;
      el.textContent = text.slice(0, at);
      let wait = deleting ? 26 : 52;
      if (!deleting && at === text.length) {
        deleting = true;
        wait = 2100;
      } else if (deleting && at === 0) {
        deleting = false;
        line = (line + 1) % lines.length;
        wait = 420;
      }
      setTimeout(step, wait);
    };
    setTimeout(step, 700);
  }
}

/* ------------------------------------------------------------------ count */

function count() {
  const items = [...document.querySelectorAll('[data-count]')];
  if (!items.length || reduced || !('IntersectionObserver' in window)) return;

  const run = (el) => {
    const target = Number(el.dataset.count);
    if (!Number.isFinite(target)) return;
    const suffix = el.dataset.suffix || '';
    const start = performance.now();
    const tick = (now) => {
      const p = Math.min(1, (now - start) / 1300);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * eased).toLocaleString('en-US') + suffix;
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };

  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        run(entry.target);
        observer.unobserve(entry.target);
      }
    },
    { threshold: 0.6 },
  );
  items.forEach((el) => observer.observe(el));
}

/* ------------------------------------------------------------------- copy */

function copy() {
  for (const pre of document.querySelectorAll('pre')) {
    if (pre.closest('[data-nocopy]')) continue;
    let box = pre.parentElement;
    if (!box.classList.contains('code')) {
      box = document.createElement('div');
      box.className = 'code';
      pre.replaceWith(box);
      box.append(pre);
    }
    if (box.querySelector('.copy')) continue;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'copy';
    button.textContent = 'Copy';
    button.addEventListener('click', async () => {
      // Shell prompts are decoration, not part of the command.
      const text = pre.innerText.replace(/^\s*(\$|PS>|>) /gm, '');
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = 'Copied';
        button.dataset.done = 'true';
      } catch {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        button.textContent = 'Selected';
      }
      setTimeout(() => {
        button.textContent = 'Copy';
        delete button.dataset.done;
      }, 1800);
    });
    box.append(button);
  }
}

/* --------------------------------------------------------------- showcase */

function showcase() {
  const items = [...document.querySelectorAll('.showcase')];
  if (!items.length || reduced) return;

  let ticking = false;
  const update = () => {
    ticking = false;
    const vh = window.innerHeight;
    for (const el of items) {
      const rect = el.getBoundingClientRect();
      if (rect.bottom < -100 || rect.top > vh + 100) continue;
      // 0 while the panel enters at the bottom edge, 1 once its top third is up.
      const lift = Math.min(1, Math.max(0, (vh - rect.top) / (vh * 0.62)));
      el.style.setProperty('--lift', lift.toFixed(3));
    }
  };
  window.addEventListener(
    'scroll',
    () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(update);
    },
    { passive: true },
  );
  window.addEventListener('resize', update);
  update();
}

/* ------------------------------------------------------------------- tabs */

function tabs() {
  for (const box of document.querySelectorAll('.tabs')) {
    const buttons = [...box.querySelectorAll(':scope > .tabs-list [role="tab"]')];
    const panels = [...box.querySelectorAll(':scope > .tabs-panel')];
    if (buttons.length < 2 || buttons.length !== panels.length) continue;
    const counter = box.querySelector('.tabs-count');
    const two = (n) => String(n).padStart(2, '0');
    let index = 0;

    const show = (next, focus) => {
      index = (next + buttons.length) % buttons.length;
      buttons.forEach((button, i) => {
        button.setAttribute('aria-selected', String(i === index));
        button.tabIndex = i === index ? 0 : -1;
      });
      panels.forEach((panel, i) => {
        panel.hidden = i !== index;
      });
      if (counter) counter.textContent = `${two(index + 1)} / ${two(buttons.length)}`;
      if (focus) buttons[index].focus({ preventScroll: true });
      // Keep the chosen tab in sight when the row scrolls sideways, without moving the page.
      const list = buttons[index].parentElement;
      const left = buttons[index].offsetLeft - list.offsetLeft;
      if (left < list.scrollLeft || left + buttons[index].offsetWidth > list.scrollLeft + list.clientWidth) {
        list.scrollTo({ left: left - 16, behavior: reduced ? 'auto' : 'smooth' });
      }
    };

    buttons.forEach((button, i) => button.addEventListener('click', () => show(i)));
    box.querySelector('.tabs-list').addEventListener('keydown', (event) => {
      const move = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: buttons.length - 1 }[event.key];
      if (move === undefined) return;
      event.preventDefault();
      show(move, true);
    });
    for (const step of box.querySelectorAll('[data-tabs-step]')) {
      step.addEventListener('click', () => show(index + Number(step.dataset.tabsStep)));
    }
    show(0);
  }
}

/* -------------------------------------------------------------- coverflow */

function coverflow() {
  const rows = [...document.querySelectorAll('.coverflow')];
  if (!rows.length || reduced) return;

  for (const row of rows) {
    let ticking = false;
    const update = () => {
      ticking = false;
      const box = row.getBoundingClientRect();
      const middle = box.left + box.width / 2;
      for (const item of row.children) {
        const rect = item.getBoundingClientRect();
        // Distance from the middle, in card widths: 0 is centre stage.
        const d = Math.max(-1.6, Math.min(1.6, (rect.left + rect.width / 2 - middle) / rect.width));
        item.style.setProperty('--ry', `${(d * -34).toFixed(2)}deg`);
        item.style.setProperty('--tz', `${(-Math.abs(d) * 150).toFixed(1)}px`);
        item.style.setProperty('--dim', (1 - Math.min(0.55, Math.abs(d) * 0.42)).toFixed(3));
      }
    };
    row.addEventListener(
      'scroll',
      () => {
        if (ticking) return;
        ticking = true;
        requestAnimationFrame(update);
      },
      { passive: true },
    );
    window.addEventListener('resize', update);
    // Start on the second picture when there are three or more: both neighbours show.
    if (row.children.length >= 3) {
      const second = row.children[1];
      row.scrollLeft = second.offsetLeft - (row.clientWidth - second.clientWidth) / 2;
    }
    update();

    // A picture at the side comes to the front when it is clicked.
    row.addEventListener('click', (event) => {
      const item = [...row.children].find((child) => child.contains(event.target));
      if (!item) return;
      const box = row.getBoundingClientRect();
      const rect = item.getBoundingClientRect();
      const offset = rect.left + rect.width / 2 - (box.left + box.width / 2);
      if (Math.abs(offset) > rect.width * 0.25) row.scrollBy({ left: offset, behavior: 'smooth' });
    });

    const nav = row.parentElement.querySelector('.coverflow-nav');
    if (nav) {
      nav.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-dir]');
        if (!button) return;
        const step = row.children[0].getBoundingClientRect().width + 16;
        row.scrollBy({ left: Number(button.dataset.dir) * step, behavior: 'smooth' });
      });
    }
  }
}

/* ------------------------------------------------------------------ stage */

function stages() {
  const holders = [...document.querySelectorAll('.stage[data-scene]')];
  if (!holders.length) return;

  const load = async (holder) => {
    if (holder.dataset.loading) return;
    holder.dataset.loading = 'true';
    try {
      const sceneUrl = new URL(holder.dataset.scene, document.baseURI).href;
      const [{ createStage }, scene] = await Promise.all([import('./stage.js'), import(sceneUrl)]);
      const stage = createStage(holder, { mode: holder.dataset.mode || 'hero', reduced });
      if (!stage) return;
      await scene.default(stage);
      stage.start();
    } catch (error) {
      // No WebGL, or a blocked script: the CSS glow behind the stage stays.
      holder.dataset.ready = 'false';
      console.warn('[aqx] 3D scene not started:', error);
    }
  };

  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 200));
  if (!('IntersectionObserver' in window)) {
    holders.forEach((holder) => idle(() => load(holder)));
    return;
  }
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        idle(() => load(entry.target));
      }
    },
    { rootMargin: '200px' },
  );
  holders.forEach((holder) => observer.observe(holder));
}

/* ------------------------------------------------------------------- boot */

for (const part of [sky, nav, reveal, tilt, magnet, typed, count, copy, showcase, tabs, coverflow, stages]) {
  try {
    part();
  } catch (error) {
    console.warn('[aqx]', part.name, error);
  }
}
