/*
 * Optional: fills a download card in from the newest GitHub release.
 *
 * Only pages that include this file contact api.github.com, and only for the
 * repository named in the markup. The static links work without it: every
 * step is optional and any failure leaves the page exactly as served.
 *
 *   <div data-release="owner/repo">
 *     <a data-release-link href="(the latest release, or one of its assets)">Download</a>
 *     <b data-release-name>App.zip</b>
 *     <span data-release-meta data-suffix="Windows 10 / 11">…</span>
 *     <span data-release-version>…</span>
 *     <a data-release-sha href="…App.zip.sha256">checksum</a>
 *   </div>
 */
for (const card of document.querySelectorAll('[data-release]')) fill(card);

async function fill(card) {
  const repo = card.dataset.release;
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) return;
  try {
    const response = await fetch(`https://api.github.com/repos/${repo}/releases/latest`, {
      headers: { Accept: 'application/vnd.github+json' },
    });
    if (!response.ok) return;
    const release = await response.json();
    const assets = release.assets || [];
    const version = String(release.tag_name || '').replace(/^v/, '');

    for (const el of card.querySelectorAll('[data-release-version]')) {
      if (version) el.textContent = (el.dataset.prefix || '') + version;
    }

    for (const link of card.querySelectorAll('[data-release-link]')) {
      const wanted = link.href.split('/').pop();
      const asset = byName(assets, wanted) || (link.dataset.fallback ? bySuffix(assets, link.dataset.fallback) : null);
      if (!asset) continue;
      link.href = asset.browser_download_url;

      const scope = link.closest('[data-release-item]') || card;
      const name = scope.querySelector('[data-release-name]');
      if (name) name.textContent = asset.name;
      const meta = scope.querySelector('[data-release-meta]');
      if (meta) {
        const parts = [];
        if (version) parts.push(`Version ${version}`);
        if (asset.size) parts.push(`${(asset.size / 1048576).toFixed(1)} MB`);
        if (meta.dataset.suffix) parts.push(meta.dataset.suffix);
        meta.textContent = parts.join(' · ');
      }
      const sha = scope.querySelector('[data-release-sha]');
      const digest = byName(assets, `${asset.name}.sha256`);
      if (sha && digest) sha.href = digest.browser_download_url;
    }
  } catch {
    /* the static links stand on their own */
  }
}

function byName(assets, wanted) {
  return assets.find((asset) => asset.name === wanted);
}

function bySuffix(assets, suffixes) {
  for (const suffix of suffixes.split(',')) {
    const hit = assets.find((asset) => asset.name.toLowerCase().endsWith(suffix.trim().toLowerCase()));
    if (hit) return hit;
  }
  return null;
}
