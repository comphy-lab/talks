# talks_build

Build pipeline for the web talks: a Keynote HTML export plus
`talks-src/<slug>/talk.yaml` in, content-addressed media plus
`site/<slug>/manifest.json` out. `docs/SPEC.md` is the contract; this file
explains how the pipeline meets it.

## Commands

```bash
cd tools
uv run python -m talks_build derive-pdf --export "<Keynote HTML export folder>"   # writes <export>.pdf
uv run python -m talks_build build --export "<Keynote HTML export folder>" \
    --talk ../talks-src/<slug>/talk.yaml [--out-root <repo root>] [--pdf <flat PDF>] [--prune] [--stills]
uv run python -m talks_build downloads --talk ../talks-src/<slug>/talk.yaml \
    --export "<Keynote HTML export folder>" [--formats pdf,pptx,key]       # after build --stills
uv run python -m talks_build validate ../site/<slug>/manifest.json [--media-dir ../build/<slug>/media]
uv run python -m talks_build refresh-assets                      # after runtime JS/CSS edits; no deck export
uv run python -m talks_build refresh-assets --check               # CI freshness gate
uv run python -m talks_build upload --slug <slug> --dry-run      # prints the plan, no remote calls
uv run python -m talks_build upload --slug <slug>                # lists every R2 page first, never overwrites
uv run pytest                                                    # unit and fixture tests
TALKS_EXPORT="<export folder>" uv run pytest                     # plus checks against a real export
```

External tools: `ffmpeg`/`ffprobe`, poppler (`pdftocairo`, `pdftotext`,
`pdftohtml`, `pdffonts`), `cwebp`, and for `upload` the `cf` CLI. Text patches also need the
slide's font installed locally (TeX Live's CMU fonts are found with
`kpsewhich`, others with `fc-match`).

Owned JS/CSS filenames stay stable; HTML references carry a query fingerprint
of each file's bytes. A runtime change gets a new URL for returning browsers.
The build refreshes these references automatically; `refresh-assets` does the
same after a runtime-only edit. CI rejects stale references before deployment.

`--pdf` defaults to the flat PDF saved next to the HTML export with the same
name, as `derive-pdf` writes it; it supplies slide titles and screen-reader
text. `--stills` keeps each state's 2x image and overlay losslessly in
`build/<slug>/stills/`; `downloads` builds the PDF, PowerPoint and Keynote
copies from them (the Keynote copy needs macOS with Keynote; SPEC section 2.1)
and adds them to the manifest. `--preview 2,4`
also writes PNG previews (image, overlay, and a layout view with the media
boxes) to `build/<slug>/preview/`. Every build writes `build/<slug>/report.json`
with the encodes, patches, the YouTube rectangle, and a check of each replayed
final state against Keynote's own flattened render.

## How the export becomes states

- One click state per non-automatic event; its picture is that event's
  `baseLayer` (the slide before the event fires). Automatic events fold into
  the state they follow.
- Mid-slide `baseLayer`s are per-object trees. A final state is rendered from
  Keynote's flattened texture unless the slide needs per-object work (patches,
  YouTube); then the last per-object tree is replayed with the visibility
  builds that follow it.
- A movie plays from the state after its `apple:movie-start` event. Its
  rectangle is the `renderMovie` layer with each edge rounded to 0.1 px and
  then to whole pixels, so copies of one source at one place on consecutive
  states get the same placement key and keep playing, and every edge lies
  within about half a pixel of Keynote's (the poster frame baked into the still
  lines up with the video).
- Movies are deduplicated by a SHA-256 of their coded video packets
  (`ffmpeg -c copy -f streamhash`): Keynote stores a re-muxed copy per slide.
- `overlay` holds the layers above a playing movie, clipped to its rectangle,
  on transparency; `null` when nothing visible overlaps. Where those layers
  have ink within 16 px of the rectangle's edge, a band 16 px wide outside
  the edge repeats the state's still, opaque (never over another media
  rectangle). A scaled stage blends the overlay's edge with what lies beyond
  it; the band keeps that blend away from the movie, so no hairline of the
  movie shows (`tests/test_media.py`, the seam test).

## Slide text and titles

`title` and `text` come from the flat PDF export, read as a viewer sees the
slide:

- Keynote's export splits each embedded TeX font into a MacRoman copy and a
  `Custom` copy whose glyphs it re-encodes from code 33. The `Custom` copy's
  `/Differences` name the glyphs correctly (`/beta`, `/infinity`), but its
  `ToUnicode` map reads each code as the original TeX position (code 33 is
  omega in CMMI), so `pdftotext` turned `β` into `ω` and `≫` into `→`.
  `tounicode.py` writes a copy of the PDF (cached under `build/<slug>/cache/`)
  whose wrong maps are rebuilt from the glyph names; unknown names become
  U+FFFD and are dropped, never guessed. The build logs how many fonts it
  repaired and `report.json` lists examples.
- Words painted at zero opacity (`pdftohtml -xml` reports the opacity) are
  left out: Beamer pages keep later overlays in place, transparent.
- A raised one-symbol block (a TeX degree sign) rejoins its line, and TeX's
  two-glyph long arrows become one character (`⇐⇒` is `⟺`).
- `titles:` in `talk.yaml` overrides a slide's derived title.

## talk.yaml decisions

- `youtube[]`: the movie whose asset name starts with `movie` is left out of
  every state image of that slide and replaced, while it plays, by the embed.
  In a state where Keynote shows the movie's poster before the click that
  starts it, `before_start: poster` (the default) draws that poster fitted
  into the embed rectangle; `before_start: embed` mounts the embed there
  already.
  With `rect: null` the build picks the largest 16:9 rectangle clear of every
  other object and text line (16 px margin) and logs it; copy it into
  `talk.yaml` to pin it. Text that was legible only against the removed movie
  (WCAG contrast under 3:1 against what is now behind it) is redrawn dark.
- `patches[]`: the `find` text is located in the slide's textures, erased, and
  redrawn as `replace` in the font that best matches the original glyphs, at
  the fitted size, colour and baseline. It wraps at a comma or moves when it
  would leave the canvas or hit other text, and is drawn above everything if an
  opaque layer would hide it. The slide's `text` gets the same correction.

## Upload

`upload` lists the keys under `talks/<slug>/` page by page with
`--per-page 1000 --start-after <last key>` until a page brings no new key.
The `cf` CLI prints only a response's `result` array, so the API's
`result_info` cursor never reaches the caller (cf 1.0.0-beta.12); stopping on
an empty page also does not depend on the page size the server honours. The
slug must match `talk.yaml`'s slug rule and the manifest's `slug`.

## Determinism

Same inputs and tool versions give the same bytes: page renders, compositing
and `cwebp` are deterministic, and `libx264` runs with a fixed thread count and
bit-exact muxing. Encodes and page renders are cached under
`build/<slug>/cache/`, keyed by content.
