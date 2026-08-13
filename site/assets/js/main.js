/* ==========================================================================
   Seiten-Interaktionen: Navigation, Tabs, Copy-Buttons, Reveal, Terminal-Demo
   ========================================================================== */

(function () {
  "use strict";

  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* -------------------------------------------------------------- Navigation */

  var nav = document.querySelector(".nav");
  var links = document.querySelector(".nav__links");
  var burger = document.querySelector(".nav__burger");

  if (burger && links) {
    burger.addEventListener("click", function () {
      var open = links.classList.toggle("is-open");
      burger.setAttribute("aria-expanded", open ? "true" : "false");
    });
    links.addEventListener("click", function (e) {
      if (e.target.tagName === "A") {
        links.classList.remove("is-open");
        burger.setAttribute("aria-expanded", "false");
      }
    });
  }

  if (nav) {
    var onScroll = function () {
      nav.classList.toggle("is-stuck", window.scrollY > 8);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* Aktiven Abschnitt in der Navigation markieren */
  var navLinks = [].slice.call(document.querySelectorAll('.nav__links a[href^="#"]'));
  var sections = navLinks
    .map(function (a) { return document.querySelector(a.getAttribute("href")); })
    .filter(Boolean);

  if (sections.length && "IntersectionObserver" in window) {
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        navLinks.forEach(function (a) {
          a.classList.toggle("is-active", a.getAttribute("href") === "#" + entry.target.id);
        });
      });
    }, { rootMargin: "-45% 0px -50% 0px", threshold: 0 });
    sections.forEach(function (s) { spy.observe(s); });
  }

  /* ------------------------------------------------------------------- Tabs */

  [].forEach.call(document.querySelectorAll("[data-tabs]"), function (group) {
    var buttons = [].slice.call(group.querySelectorAll("[role=tab]"));
    var panels = [].slice.call(document.querySelectorAll('[data-tabgroup="' + group.dataset.tabs + '"]'));

    function select(id) {
      buttons.forEach(function (b) { b.setAttribute("aria-selected", String(b.dataset.tab === id)); });
      panels.forEach(function (p) { p.hidden = p.dataset.tabpanel !== id; });
    }

    buttons.forEach(function (b) {
      b.addEventListener("click", function () { select(b.dataset.tab); });
      b.addEventListener("keydown", function (e) {
        var i = buttons.indexOf(b);
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          e.preventDefault();
          var next = buttons[(i + (e.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length];
          next.focus();
          select(next.dataset.tab);
        }
      });
    });

    /* Betriebssystem erkennen und den passenden Reiter vorauswählen */
    if (group.dataset.tabs === "os") {
      var ua = navigator.userAgent || "";
      var os = /Mac|iPhone|iPad/i.test(ua) ? "mac" : (/Linux|X11|Android/i.test(ua) && !/Windows/i.test(ua) ? "linux" : "windows");
      if (buttons.some(function (b) { return b.dataset.tab === os; })) select(os);
    }
  });

  /* --------------------------------------------------------- Copy-Buttons */

  [].forEach.call(document.querySelectorAll(".copy"), function (btn) {
    btn.addEventListener("click", function () {
      var pre = btn.closest(".code").querySelector("pre");
      var text = pre.innerText.replace(/^\s*[\r\n]/gm, "\n").trim();
      var done = function () {
        var old = btn.textContent;
        btn.textContent = "KOPIERT";
        btn.classList.add("is-done");
        setTimeout(function () { btn.textContent = old; btn.classList.remove("is-done"); }, 1600);
      };

      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done, fallbackCopy);
      } else {
        fallbackCopy();
      }

      function fallbackCopy() {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); done(); } catch (e) { /* still */ }
        document.body.removeChild(ta);
      }
    });
  });

  /* ----------------------------------------------------------- Reveal */

  var revealables = [].slice.call(document.querySelectorAll(".reveal"));
  if (reduced || !("IntersectionObserver" in window)) {
    revealables.forEach(function (el) { el.classList.add("is-in"); });
  } else {
    var ro = new IntersectionObserver(function (entries, obs) {
      entries.forEach(function (entry, i) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        setTimeout(function () { el.classList.add("is-in"); }, Math.min(i * 60, 240));
        obs.unobserve(el);
      });
    }, { rootMargin: "0px 0px -12% 0px", threshold: 0.06 });
    revealables.forEach(function (el) { ro.observe(el); });
  }

  /* -------------------------------------------------------- Terminal-Demo */

  var term = document.getElementById("terminal-body");
  if (term) {
    var script = [
      { cls: "m", text: "$ python jarvis.py" },
      { cls: "m", text: "  [OK] Server läuft auf http://127.0.0.1:8765", delay: 420 },
      { cls: "u", text: "Sir: Sieh dir meinen Bildschirm an", type: true },
      { cls: "j", text: "JARVIS: Ein Merge-Konflikt in core/brain.py, Zeile 214.", delay: 260 },
      { cls: "u", text: "Sir: Merk dir, dass ich Python bevorzuge", type: true },
      { cls: "j", text: "JARVIS: Gespeichert. Verschlüsselt in ~/.jarvis/memory.", delay: 260 },
      { cls: "u", text: "Sir: Gib mir mein Briefing", type: true },
      { cls: "j", text: "JARVIS: 18°C, 3 Termine, 2 offene Tasks, 5 ungelesene Mails.", delay: 260 }
    ];

    var caret = document.createElement("span");
    caret.className = "caret";
    var step = 0;

    function nextLine() {
      if (step >= script.length) {
        setTimeout(function () { term.innerHTML = ""; step = 0; nextLine(); }, 5200);
        return;
      }
      var item = script[step++];
      var line = document.createElement("div");
      line.className = item.cls;
      term.appendChild(line);
      term.appendChild(caret);

      if (item.type && !reduced) {
        var i = 0;
        (function tick() {
          line.textContent = item.text.slice(0, ++i);
          if (i < item.text.length) setTimeout(tick, 26);
          else setTimeout(nextLine, 620);
        })();
      } else {
        line.textContent = item.text;
        setTimeout(nextLine, reduced ? 900 : (item.delay || 520));
      }
    }

    if (reduced) {
      term.innerHTML = script.map(function (i) {
        return '<div class="' + i.cls + '">' + i.text + "</div>";
      }).join("");
    } else {
      setTimeout(nextLine, 500);
    }
  }

  /* ------------------------------------------------------------------ Jahr */

  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());
})();
