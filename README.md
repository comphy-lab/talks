# CoMPhy Lab talks

Web versions of CoMPhy Lab project talks, served at
<https://comphy-lab.org/talks/>. Each project has one living talk at
`comphy-lab.org/talks/<project-slug>/`, updated as the work moves on.

The pages carry comphy-lab.org's header and footer (`docs/SPEC.md`,
Section 5). A talk is played by a small static player (`site/player/`), not
by Keynote's HTML export. Each click state is one image. Real `<video>` elements sit on top
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
  vendor/comphy-lab/  comphy-lab.org header and footer styles and logos (vendored)
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
   uv run python -m talks_build derive-pdf --export "<path to export>"
   uv run python -m talks_build build --export "<path to export>" --talk ../talks-src/<slug>/talk.yaml --stills
   uv run python -m talks_build downloads --talk ../talks-src/<slug>/talk.yaml  # register Dropbox entries from talk.yaml
   uv run python -m talks_build validate ../site/<slug>/manifest.json
   uv run python -m talks_build upload --slug <slug>    # needs the cf CLI, logged in
   ```

   Store the downloadable PDF, PowerPoint and Keynote copies in Dropbox.
   List them in `talk.yaml` as `{format, url, bytes}` entries, with public
   file links using `dl=1`. `downloads` registers those links in the manifest.
   Updating links on an existing talk needs only `downloads` and `validate`;
   it needs neither a Keynote export nor macOS.

   To generate local copies first, use format names in `talk.yaml` and run
   `downloads --export "<path to export>"` after `build --stills`. Generating
   the Keynote copy needs macOS with Keynote. Add the generated files to
   Dropbox and register their links before publishing; R2 holds slide media.

4. Run the browser tests (`tests/README.md`), then commit `site/<slug>/` and
   push. GitHub Actions validates the manifests and deploys `site/`.

## Credits and rights

Slides contain figures and movies from other groups. Each is credited on the
slide where it appears. The player code and lab-authored content are by the
CoMPhy Lab; third-party material remains its owners'. Design-system fonts are
under the SIL Open Font License (`site/vendor/ds/fonts/OFL.txt`).
