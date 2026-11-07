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

## Publishing a talk

- Export HTML from a copy of the accepted `.key` (delete web-excluded
  slides; set Magic Move to Dissolve and list it under `magic_move` in
  `talk.yaml`, so the player replays it), then `derive-pdf`, `build --stills`,
  `validate`, both test suites and `upload` (`docs/SPEC.md` Sections 1-2).
- Downloads: put the files in Dropbox and register the public `dl=1` links
  that Vatsal creates. The Keynote download is the native deck (Section 2.1).
  A format appears on the page only once its link is registered.
- If the Pages deploy job fails, do not use "Re-run failed jobs": it leaves
  two `github-pages` artifacts on the run, which then cannot deploy. Start a
  fresh run with `gh workflow run pages.yml --ref main`.
- After the deploy, compare the live `manifest.json` and `talks.json` with
  the commit; Cloudflare rewrites only the footer's email link in the HTML.

## Development

```bash
cd tools && uv run pytest                     # pipeline unit tests
cd tests && uv run pytest                     # browser tests (Chromium + WebKit)
python3 -m http.server -d site 8000           # local preview at /<slug>/
```
