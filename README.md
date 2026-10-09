# CoMPhy Lab talks

Web versions of CoMPhy Lab project talks, served at
<https://comphy-lab.org/talks/>. Each project has one living talk at
`comphy-lab.org/talks/<project-slug>/`, updated as the work moves on.

A talk is played by a small static player (`site/player/`), not by Keynote's
HTML export. Each click state is one image. Real `<video>` elements sit on top
of the image, and a video that continues across slides is a single element that
keeps playing. Slide images and videos are content-addressed files on
Cloudflare R2 (`media.comphy-lab.org`); this repository holds only code,
manifests and the human decisions behind each talk.

## Layout

```
site/                 deployed to GitHub Pages
  index.html          talks index
  player/             player (ES module, CSS, page template)
  vendor/ds/          CoMPhy design-system tokens and fonts (vendored)
  <slug>/             one talk: index.html + manifest.json (generated)
talks-src/<slug>/     talk.yaml: decisions the Keynote export does not record
tools/                build pipeline (Python): export -> media + manifest -> R2
tests/                pipeline unit tests and browser tests (Playwright)
docs/SPEC.md          manifest schema and player behaviour
```

## Adding or updating a talk

1. In Keynote, File > Export To > HTML.
2. Write or update `talks-src/<slug>/talk.yaml`.
3. Build, check and upload:

   ```bash
   cd tools
   uv run python -m talks_build build --export "<path to export>" --talk ../talks-src/<slug>/talk.yaml
   uv run python -m talks_build validate ../site/<slug>/manifest.json
   uv run python -m talks_build upload --slug <slug>    # needs the cf CLI, logged in
   ```

4. Run the browser tests (`tests/README.md`), then commit `site/<slug>/` and
   push. GitHub Actions validates the manifests and deploys `site/`.

## Credits and rights

Slides contain figures and movies from other groups. Each is credited on the
slide where it appears. The player code and lab-authored content are by the
CoMPhy Lab; third-party material remains its owners'. Design-system fonts are
under the SIL Open Font License (`site/vendor/ds/fonts/OFL.txt`).
