# My Jarvis — website

The site at <https://mybluejarvis.vercel.app>. Plain HTML, CSS and JavaScript: no build
step, no framework, nothing loaded from another host.

| File | What it is |
| --- | --- |
| `index.html` | Landing page |
| `features.html` | Features |
| `setup.html` | Setup |
| `download.html` | Download |
| `terms.html` | Terms of Use |
| `404.html` | Shown for any address that does not exist |
| `assets/site.css` | This project's accent colour and anything only this site needs |
| `assets/scene.js` | The 3D scene in the page header |
| `assets/aqx/` | The look and behaviour shared by all AquAxs project sites |

## The shared part

`assets/aqx/` is the same in every project site: the stylesheet (`aqx.css`),
the interactions (`aqx.js`), the 3D stage (`stage.js`), three.js and the three
typefaces. It follows the portfolio at aqua.is-a.dev: a black sky with slow
dots, glass cards that lean towards the pointer, Bodoni Moda headlines.
Change the accent in `assets/site.css` (`--ac`, `--ac-hi`), not in `aqx.css`.

## The 3D scene

`assets/scene.js` exports one function that builds its objects into
`stage.frame`. The stage (`assets/aqx/stage.js`) does the rest: camera, lights,
resizing, pointer and touch, pausing off screen. With reduced motion the scene
is one still frame; without WebGL a glow in the accent colour stays instead.

## Preview

Links have no `.html` (Vercel serves `/download` from `download.html`), so open
the folder through a server that does the same:

```bash
npx serve site
```

## Deploy

Vercel, root directory `site`, no build command. `vercel.json` sets clean URLs
and the security headers. The site sets no cookies and stores nothing.

## The 3D scene and the terminal

`assets/scene.js` replaces the old hand-written raymarcher with the shared three.js stage: the
core breathes, three rings turn, and a click runs one exchange from the demo — listening in
teal, thinking in amber, the colours of the app's own status orb. `assets/terminal.js` types
the session in the panel next to "Not a chat window"; the same lines are in the HTML for
anyone without JavaScript.

There is no inline script any more, so the CSP needs no hash: if you add one, give it its own
file instead.
