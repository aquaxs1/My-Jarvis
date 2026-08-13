# My Jarvis — Website

Statische Landingpage für JARVIS: Installation, Funktionen und Bedienung.
Kein Build-Schritt, keine Abhängigkeiten, kein CDN — reines HTML, CSS und JavaScript.

## Inhalt

```
site/
  index.html              komplette Seite (Hero, Funktionen, Installation, Nutzung, Sicherheit, Hilfe)
  favicon.svg
  robots.txt
  vercel.json             Cache- und Security-Header
  assets/
    css/style.css
    js/core.js            3D-Kern: Raymarching-Shader in purem WebGL
    js/main.js            Navigation, Tabs, Copy-Buttons, Reveal, Terminal-Demo
```

Die 3D-Grafik im Hero ist ein Distance-Field-Raymarcher, der direkt im Fragment-Shader läuft:
ein pulsierender Energiekern mit drei rotierenden Ringen, Sternenfeld und Maus-Parallaxe.
Er skaliert die Auflösung automatisch herunter, wenn die GPU nicht mitkommt, pausiert außerhalb
des Sichtbereichs, respektiert `prefers-reduced-motion` und fällt ohne WebGL auf eine
CSS-Animation zurück.

## Lokal ansehen

```bash
cd site
python3 -m http.server 4321
# oder: npx http-server -p 4321
```

Dann `http://localhost:4321` öffnen.

## Deployment auf Vercel

Die Seite ist eine reine Static-Site — kein Framework, kein Build-Command.

### Variante 1: Git-Integration (empfohlen)

1. [vercel.com/new](https://vercel.com/new) öffnen und das Repository `aquaxs1/My-Jarvis` importieren
2. **Root Directory** auf `site` setzen — das ist der entscheidende Schritt,
   sonst sucht Vercel im Repository-Root nach einem Projekt
3. Framework Preset: **Other**, Build Command und Install Command leer lassen
4. **Deploy** klicken

Jeder weitere Push auf den Branch löst automatisch ein neues Deployment aus.

### Variante 2: CLI

```bash
npm i -g vercel
cd site
vercel          # Vorschau-Deployment
vercel --prod   # Produktion
```

Bei der ersten Ausführung fragt die CLI nach Projektname und Einstellungen —
Build Command und Output Directory bleiben leer.
