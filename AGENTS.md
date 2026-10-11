# CoMPhy Lab talks

Static web player for CoMPhy project talks at `comphy-lab.org/talks/<slug>/`.
`docs/SPEC.md` is the contract for the pipeline, player and tests; read it
before changing any of them.

## Rules

- Media never enters Git. Slide images and videos live on R2
  (`media.comphy-lab.org/talks/<slug>/`) under content-hash names. Never
  overwrite an existing key; a changed file gets a new name.
- `site/<slug>/manifest.json` is generated. Change `talks-src/<slug>/talk.yaml`
  or the pipeline, then rebuild; do not hand-edit manifests. A wrong slide
  title is fixed with `titles:` in `talk.yaml`, never in the manifest.
- Player code builds DOM with `createElement`/`textContent` only, with no
  `innerHTML`, no inline scripts or styles, and no third-party scripts except
  the YouTube IFrame API loaded on demand. Keep the per-page CSP in
  `site/player/talk.template.html` in step with `docs/SPEC.md`.
- Follow the CoMPhy design system: vendored `site/vendor/ds/` tokens and fonts,
  with no font CDNs. Teal is the player interactive accent; the shared site
  header and footer follow comphy-lab.org's explicit shell colours. Dark mode uses
  `data-theme` with `localStorage` key `theme`.
- A talk is published only when Vatsal says so. No unpublished results without
  his explicit approval.
- Citations shown on slides come from publisher metadata (Crossref), never
  from memory.

## Development

```bash
cd tools && uv run pytest                     # pipeline unit tests
cd tests && uv run pytest                     # browser tests (Chromium + WebKit)
python3 -m http.server -d site 8000           # local preview at /<slug>/
```
