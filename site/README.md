# JARVIS — Website

Static site for JARVIS: what it does, how to install it, how to use it.
No build step, no dependencies, no CDN — plain HTML, CSS and JavaScript.

## Contents

```
site/
  index.html              landing page (hero, overview, install, usage, safety, help)
  features.html           all 20 modules, integration requirements, optional packages
  favicon.svg
  robots.txt
  vercel.json             cache and security headers
  assets/
    css/style.css
    js/core.js            3D core: raymarching shader in plain WebGL
    js/main.js            nav, tabs, copy buttons, reveal, terminal demo
```

The hero graphic is a distance-field raymarcher running entirely in a fragment shader:
a pulsing energy core with three rotating rings, a starfield and mouse parallax.
It scales its own resolution down when the GPU struggles, pauses when off-screen,
honours `prefers-reduced-motion`, and falls back to a CSS animation without WebGL.

The icon sprite is inlined at the top of each page — keep the two copies in sync when
adding icons.

## Run locally

```bash
cd site
python3 -m http.server 4321
# or: npx http-server -p 4321
```

Then open `http://localhost:4321`.

## Deploy to Vercel

Pure static site — no framework, no build command.

### Option 1: Git integration (recommended)

1. Open [vercel.com/new](https://vercel.com/new) and import `aquaxs1/My-Jarvis`
2. Set **Root Directory** to `site` — this is the step that matters,
   otherwise Vercel looks for a project in the repository root
3. Framework preset: **Other**. Leave build and install commands empty
4. Click **Deploy**

Every later push to the branch redeploys automatically.

### Option 2: CLI

```bash
npm i -g vercel
cd site
vercel          # preview deployment
vercel --prod   # production
```

On the first run the CLI asks for a project name and settings — leave build command
and output directory empty.
