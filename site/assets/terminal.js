/*
 * My Jarvis — the terminal demo next to "Not a chat window".
 *
 * Types a short session line by line, then starts over. With reduced motion
 * the whole session is shown at once. Decorative: the panel is aria-hidden,
 * and the same lines are in the page as text for anyone without script.
 */
(function () {
  'use strict';

  var term = document.getElementById('terminal-body');
  if (!term) return;
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var script = [
    { cls: 'm', text: '$ python jarvis.py' },
    { cls: 'm', text: '  [OK] Server running on http://127.0.0.1:8765', delay: 420 },
    { cls: 'u', text: 'Sir: Look at my screen', type: true },
    { cls: 'j', text: 'JARVIS: A merge conflict in core/brain.py, line 214.', delay: 260 },
    { cls: 'u', text: 'Sir: Remember that I prefer Python', type: true },
    { cls: 'j', text: 'JARVIS: Stored, encrypted, in ~/.jarvis/memory.', delay: 260 },
    { cls: 'u', text: 'Sir: Give me my briefing', type: true },
    { cls: 'j', text: 'JARVIS: 18°C, 3 events, 2 open tasks, 5 unread emails.', delay: 260 },
  ];

  function line(item) {
    var el = document.createElement('div');
    el.className = 't-' + item.cls;
    return el;
  }

  if (reduced) {
    term.textContent = '';
    script.forEach(function (item) {
      var el = line(item);
      el.textContent = item.text;
      term.appendChild(el);
    });
    return;
  }

  var caret = document.createElement('span');
  caret.className = 't-caret';
  var step = 0;

  function next() {
    if (step >= script.length) {
      setTimeout(function () {
        term.textContent = '';
        step = 0;
        next();
      }, 5200);
      return;
    }
    var item = script[step++];
    var el = line(item);
    term.appendChild(el);
    term.appendChild(caret);
    if (item.type) {
      var i = 0;
      (function tick() {
        el.textContent = item.text.slice(0, ++i);
        if (i < item.text.length) setTimeout(tick, 26);
        else setTimeout(next, 620);
      })();
    } else {
      el.textContent = item.text;
      setTimeout(next, item.delay || 520);
    }
  }

  term.textContent = '';
  setTimeout(next, 500);
})();
