# Talks site specification (v2)

This is the contract between the three parts of the repository:

- `tools/`: the build pipeline. It turns a Keynote HTML export plus a
  human-authored `talks-src/<slug>/talk.yaml` into media files and a
  manifest.
- `site/`: the static player and the talks index, deployed by GitHub Pages
  at `https://comphy-lab.org/talks/`, inside comphy-lab.org's header and
  footer (Section 5).
- `tests/`: unit tests for the pipeline and browser tests for the player.

Media (slide images and videos) is hosted on Cloudflare R2 at
`https://media.comphy-lab.org/talks/<slug>/`. Files are named by content hash,
so a URL never changes meaning and can be cached forever. The repository holds
code, manifests and decisions, never media.

## 1. Inputs

### 1.1 Keynote HTML export

The Keynote HTML export (File > Export To > HTML) is the only build input
for slide content. The export is not committed: it is large, and most of it
is duplicated. Its structure:

- `assets/header.json`: `slideList` (slide UUIDs in show order),
  `slideWidth`, `slideHeight` (1920 x 1080 for our decks).
- `assets/<UUID>/<UUID>.json`: per slide. `events[]` are the build steps.
  - Each event has a `baseLayer`: the static state *before* the event fires,
    as a tree of layers with `initialState` (position, bounds, anchor point,
    opacity, hidden) and `texture` references.
  - Each event has `effects[]`: `buildIn` / `buildOut` / `transition`, with
    nested effects. Movies appear as `apple:movie-start` buildIn effects
    wrapping a `renderMovie` effect, which carries
    `movie {asset, startTime, endTime, loopMode, ...}`.
  - Each event has `automaticPlay`: the event fires without a click.
  - The last event of every slide is its outgoing transition. Its `baseLayer`
    is one flattened texture of the slide's final state.
  - `assets` maps ids to textures: `{type: texture, index: <page>, url.native:
    assets/<UUID>.pdf}`, i.e. page N of a per-slide PDF. Movie ids map to
    `{type: video, url.native: <file>, width, height}`.
- `assets/<UUID>.pdf`: the per-slide texture atlas, vector, one page per
  texture. The `.pdfp` and `.jsonp` copies exist only for `file://` loading;
  ignore them.

### 1.2 `talks-src/<slug>/talk.yaml`

This file holds the human decisions the export does not record. See the file
for the authoritative example. Its fields:

- `slug`, `title`, `subtitle`, `speaker`, `venue_note`, `updated`
  (ISO date), `media_base`.
- `video.loop: true`: every video loops.
- `video.spans: auto`: the same source at the same rectangle on consecutive
  click states, and across consecutive slides, is one continuous placement.
- `video.max_scale: 2.0`: videos are downscaled to at most twice their
  on-slide box.
- `youtube[]`: movies replaced by a YouTube embed. Each entry gives the slide
  UUID, the source movie name prefix, the YouTube id, start and end in seconds,
  the embed rectangle, and the attribution text. `before_start` says what a
  click state shows when Keynote shows the movie's poster before the click
  that starts it: `poster` (default) draws that poster frame, fitted into the
  embed rectangle, as Keynote does; `embed` mounts the embed already, so it
  plays from that state on.
- `patches[]`: corrections drawn onto slide images. Each gives the slide UUID,
  the rectangle to cover, the replacement text and style. They are used until
  the `.key` file itself is fixed.
- `titles`: optional map from slide UUID to a human-written title, for a
  slide whose title derived from its text (Section 3) is wrong.
- `downloads`: optional list of the downloadable copies the talk page offers,
  in display order: any of `pdf`, `pptx`, `key` (Section 2.1).
- `changelog[]`: `{date, note}`.

## 2. Pipeline outputs

`python -m talks_build build --export <dir> --talk talks-src/<slug>/talk.yaml`
writes:

- `build/<slug>/media/<sha256-16>.<ext>`: content-addressed files (`webp`
  stills and overlays, `mp4` videos, `webp` posters). `build/` is gitignored.
- `site/<slug>/manifest.json`: the manifest (Section 3), which is committed.
- `site/<slug>/index.html`: the talk page, generated from
  `site/player/talk.template.html`.

Every state is rendered at both 1x (1920 x 1080) and 2x (3840 x 2160).
The texture PDFs are rasterised at 72 and 144 dpi respectively; the higher
density is a new vector render, not an enlargement of the 1x image. Geometry
is kept in logical canvas pixels and snapped before multiplying when drawing.
Text patches share their fitted font and logical layout between densities,
with glyphs drawn at the target size. Videos and their posters retain their
source resolution and are not enlarged for this second image density.

With `--stills`, the build also keeps the 2x image and overlay of every
state losslessly, as `build/<slug>/stills/<webp name stem>.png`, for the
PowerPoint and Keynote downloads.

The flat PDF the build reads for slide text (`--pdf`, by default `<export>.pdf`
next to the export folder) is written by
`python -m talks_build derive-pdf --export <dir>`: one page per slide, the
slide's final flattened texture from the export's atlas, cropped to the
1920 x 1080 canvas and kept vector. It is derived from the export, not
Keynote's own PDF export.

`python -m talks_build upload --slug <slug>` uploads to R2 every file in
`build/<slug>/media/` not already present that the manifest references, under
`talks/<slug>/<file>` (a download under `talks/<slug>/<sha256-16>/<name>`),
using the `cf` CLI with the correct `Content-Type`. Because files are
content-addressed, an existing key is never overwritten. A file over the CLI's
single-request limit of 300 MB is refused. The slug must be a valid talk slug and equal the manifest's
`slug`. The existing keys are listed page by page with `--start-after` (the
`cf` CLI does not pass on the API's cursor) until a page brings no new key;
if the listing fails or does not finish, nothing is uploaded.

`python -m talks_build validate site/<slug>/manifest.json` checks the
manifest against `tools/schema/manifest.schema.json` and the invariants in
Section 3.3.

### 2.1 Downloads

`python -m talks_build downloads --talk talks-src/<slug>/talk.yaml --export <dir>`
runs after a `build --stills` and makes the formats listed in the talk's
`downloads` (`--formats` may choose a subset of them). It refuses unless the
site manifest is the one the last build wrote and, for the PDF, `--export` is
the export that build read: the build records both digests in
`build/<slug>/report.json`. The downloads therefore show what the site shows.

- `pdf`: the derived PDF (above), one page per slide in its final state.
  Movies appear as the frame Keynote shows; each movie area links to that
  slide on the website. The text maps are repaired as for slide text
  (Section 3.1), so the PDF's text can be searched and copied. Title, author
  and subject are set from the manifest.
- `pptx`: every click state, 13.333 x 7.5 in (16:9; one canvas pixel is
  6350 EMU). Each state is its lossless 2x still at full size, each video at
  its manifest rectangle, and its overlay above them, so nothing is
  re-typeset or re-laid out. Videos are muted and loop. PowerPoint and
  Keynote restart a movie on every slide, so consecutive states that keep a
  video placement (the player's key, Section 3.2), within a slide or across
  slides, share one slide: each later state is a click build that shows its
  still beneath the videos and shows or hides the placements and overlays
  that change. A video that stays is one shape throughout and plays on, as on
  the web; one a click brings starts with that click. Keynote imports such a
  video's entrance and start as one Movie Start build, which shows its first
  frame from the start of the slide, so until that click it is covered by
  the showing state's still, cropped to its box. The build refuses a shared
  slide on which two overlapping placements would stack in another order
  than the player's, or a cover would hide other media. Every other state is
  a slide of its own, its videos starting with it. A YouTube placement
  becomes the poster frame Keynote shows, cropped from a state of the same
  slide without the embed, linked to the video on YouTube at its start time;
  the video itself is not included. A `dissolve` state gets a fade
  transition, or a fade-in of its still when it is a click build.
- `key`: the `pptx` file opened in Keynote (macOS only), with every movie set
  to loop and muted, and saved as a Keynote document. The build fails unless
  Keynote reports the expected numbers of slides and movies.

Each file is named `<speaker surname>-<slug>-<updated>.<ext>` and stored as
`build/<slug>/media/<sha256-16 of the file>/<name>`, so its key is
content-addressed while a browser saves it under its own name (a cross-origin
`download` attribute is ignored, and the `cf` CLI cannot set
`Content-Disposition`). The command then writes the manifest's `downloads`.
A later `build` drops them; run `downloads` again after it.

## 3. Manifest (`schema: 2`, with schema 1 compatibility)

```jsonc
{
  "schema": 2,
  "slug": "singular-bursting-bubbles",
  "title": "…", "subtitle": "…", "speaker": "…", "updated": "2026-10-10",
  "canvas": { "width": 1920, "height": 1080 },
  "media_base": "https://media.comphy-lab.org/talks/singular-bursting-bubbles/",
  "videos": {
    "v01": { "src": "<hash>.mp4", "poster": "<hash>.webp",
             "width": 1024, "height": 880, "duration": 8.27,
             "credit": null }                         // or attribution text
  },
  "youtube": {
    "y01": { "id": "a9hUsVq9q7U", "start": 7.191, "end": 63.397,
             "title": "…", "credit": "…" }
  },
  "slides": [
    {
      "id": "<Keynote slide UUID>",                  // stable deep-link key
      "n": 1,                                         // 1-based show order
      "title": "…",                                   // first text line, for UI and screen readers
      "text": "…",                                    // all text shown on the slide, for screen readers and search
      "states": [                                     // one per click (rest state)
        {
          "image":   "<hash>.webp",                   // full-canvas still, videos excluded
          "overlay": "<hash>.webp",                   // or null: transparent full-canvas layer drawn ABOVE media
          "image2x": "<hash>.webp",                   // optional 3840 x 2160 vector render
          "overlay2x": "<hash>.webp",                 // paired with image2x; null exactly when overlay is null
          "transition": "none",                       // "none" | "dissolve"
          "media": [                                  // everything visible in this state
            { "kind": "video",   "ref": "v01", "rect": [x, y, w, h] },
            { "kind": "youtube", "ref": "y01", "rect": [x, y, w, h] }
          ]
        }
      ]
    }
  ],
  "changelog": [ { "date": "2026-10-10", "note": "…" } ],
  "downloads": [                                      // optional (Section 2.1)
    { "format": "pdf", "file": "<hash>/<name>.pdf", "bytes": 52428800 }
  ]
}
```

The player and validator still accept schema 1 manifests. Schema 2 adds the
optional `image2x` / `overlay2x` pair; when absent, the original pair remains
usable at any display density. The builder always emits both densities.
`downloads` is optional in both schemas; the URL of a download is
`media_base + file`.

All rectangles are in logical canvas pixels (origin top-left). They may extend past the
canvas; the stage clips them. Media rectangles are whole pixels: the pipeline
rounds each *edge* of the Keynote rectangle (`w = round(x + w) - round(x)`),
not the size, so a video lines up with the poster frame baked into the still.

### 3.1 Still images

- `image` is the slide as it looks in that state with every movie area showing
  its poster frame. In other words, it is the Keynote render for that state;
  the video element covers the poster.
- `overlay` exists when Keynote layers sit *above* a movie (labels, masks,
  arrows). It contains only those layers on a transparent background, so the
  player can draw them over the playing video. When nothing overlaps a movie,
  `overlay` is null. Where those layers reach a movie's edge, the overlay
  continues for 16 canvas pixels past the edge with the state's own `image`
  (opaque), never over another media rectangle: a browser that scales the
  stage blends the overlay's edge with what lies beyond it, and without the
  band the movie's edge pixels would show through as a hairline.
- `image2x` and `overlay2x` show the same state at twice the pixel density.
  Their layer positions, media cut-outs and bleed band have the same logical
  dimensions as the original pair; the 16-pixel band is 32 image pixels at 2x.
  Raster content inside a PDF stays limited by the source Keynote stored.
- For a `youtube` placement, `image` must not contain the movie poster under
  the embed rectangle. YouTube forbids overlays on its player, so the
  slide's title and credit must lie outside `rect`. A state of that slide
  without the placement (before the click that starts the movie, with
  `before_start: poster`) shows the poster fitted into the same rectangle.
- `title` and `text` come from the flat PDF export, as a viewer reads the
  slide: TeX glyphs are decoded from the fonts' own glyph names where the
  export's `ToUnicode` maps contradict them, and text painted at zero
  opacity (Beamer overlays kept for later slides) is left out. `text` covers
  every click state of the slide.

### 3.2 Player semantics

- **Stage.** The player shows a 16:9 stage, scaled uniformly to fit the
  viewport. The z-order is `image`, then media, then `overlay`, then UI. Media
  outside the canvas is clipped.
- **Image density.** Use the matching `image2x` / `overlay2x` pair whenever
  `devicePixelRatio × rendered stage width` exceeds the logical canvas width,
  so the original pair is never upscaled when a 2x pair exists. Use 1x when
  it fits, or when the state has no complete 2x pair. Re-evaluate after resize,
  display-density changes and entering or leaving fullscreen; preload the
  density selected for the stage.
- **Placement key.** Each media item has the key `kind + ref + rect rounded to
  1 px`.
- **Changing state** (any navigation, including jumps):
  1. Compute the target state's set of keys.
  2. Keep, untouched, every mounted element whose key is in the target set.
     It is never re-created, re-attached or seeked, so it keeps playing.
  3. Remove any element whose key is not in the set (pause, then detach).
  4. Create elements for new keys and start them from the beginning (YouTube:
     from `start`).
  5. Swap `image` and `overlay`.

  This one rule implements builds within a slide and videos playing across
  slides. The sole layout exception is YouTube's minimum viewport size:
  crossing its 200 x 200 CSS px threshold replaces the inline player with
  the link-out or re-creates the player when enough space returns. A recreated
  YouTube player restarts at `start`. Native video elements remain untouched.
- **Video elements.** `muted`, `playsinline`, `loop`, `preload="auto"`,
  `disablepictureinpicture`, no `controls`. The `poster` is set to the video's
  poster. The `src` is `media_base + src`.
- **YouTube.** Use the IFrame Player API, loaded only when a state first needs
  it, with host `https://www.youtube-nocookie.com`, `mute: 1`, `controls: 0`,
  `playsinline: 1` and `rel: 0`. Start at `start` and stop at `end`. When it
  reaches the end (`ENDED`), seek back to `start` and play: this is the loop.
  If the API cannot load (blocked network), or the player has not reported
  ready 10 s after it was created (the embed host blocked), show the poster
  region with a "Watch on YouTube" link and the credit. A click inside the
  embed moves keyboard focus into YouTube's frame; the player returns it to
  the stage frame at once, so keys and presenter clickers keep working.
  The 200 x 200 CSS px minimum is re-evaluated after resize or fullscreen
  changes; below it, the link-out is shown without loading an inline player.
- **Pause control.** A visible button pauses and resumes all media. This
  satisfies WCAG 2.2.2. Under `prefers-reduced-motion: reduce`, media starts
  paused and the button resumes it.
- **Downloads.** When the manifest lists `downloads`, a download button
  before the pause button ("Download this talk", with `aria-expanded` and
  `aria-controls`) discloses a panel with one plain link per file (the PDF
  opens in a new tab, since browsers show rather than save it), in
  manifest order: PDF ("One page per slide; movies as still frames"),
  PowerPoint or Keynote (both "Every click as here; movies play through"), each
  with its size in decimal megabytes (whole MB; one decimal place below
  1 MB). The URL is `media_base + file`, under the test hook's override like
  other media. An entry with an unknown format, a file name that is not
  `<sha16>/<name>.<format>`, a repeated format or a size that is not a
  positive integer is left out; with no valid entry the button stays hidden.
  The panel opens above the control bar, right-aligned to the button and
  inside the viewport, without moving the stage. Escape (focus returns to
  the button), a second press of the button, a click outside the panel and
  following a link close it; a click on the stage that closes it does not
  change slide. Entering full screen closes it. While it is open the player
  takes Escape and every other key works as usual.
- **Navigation.**
  - Next: Right, Down, Space, PageDown, Enter, a click on the stage's right
    half, a swipe left.
  - Previous: Left, Up, PageUp, Backspace, a click on the left half, a swipe
    right.
  - Home and End go to the first and last state.
  - `F` toggles fullscreen. `P` and `K` toggle pause.
  - Presenter clickers send PageUp and PageDown.
  - Clicks and swipes inside a YouTube embed go to YouTube's cross-origin
    frame and cannot navigate; the page's help says to swipe outside the
    video or use the arrow buttons there.
- **Deep links.** `#<first 8 hex chars of slide UUID>` or `#<…>.<k>`, where
  `k` counts the slide's states from 1 (`#<…>.1` is the same as `#<…>`). The
  hash follows the state on screen, written without a suffix for a slide's
  first state. `history.replaceState` avoids flooding history; writes are
  coalesced to at most one per 250 ms, because WebKit throws after 100
  `replaceState` calls in 10 s (holding an arrow key reaches that).
- **Preloading.** Stills for the next two states and the previous one. Video
  elements are created only when needed.
- **Accessibility.**
  - The stage has `role="img"` with `aria-label` set to the slide title.
  - A visually hidden live region announces "Slide n of N: title" on
    reaching a slide (", step k of K" before the colon when the slide has
    K > 1 click states) and "Step k of K" on the later steps of the same
    slide.
  - The slide `text` is in a visually hidden element for screen readers.
  - All controls are keyboard reachable with a visible teal focus ring. On
    the black full-screen backdrop a light inner ring keeps the indicator
    above 3:1 in both themes.
  - While the stage fills the screen (element full screen, or the phone
    fallback that fills the viewport), the page around it, the site header
    included, is `inert`, so focus and screen readers reach only what is
    visible; focus returns to where it was on leaving. The phone fallback
    also hides the fixed site header, which would otherwise paint over it.
- **Theme.** The design system's `data-theme` on `<html>`, with
  `localStorage` key `theme` (shared with comphy-lab.org). Each page has
  exactly one theme control, the toggle in the site header (Section 5); the
  player's control bar has none. The native button has the stable accessible
  name "Dark theme" and `aria-pressed` reflects whether dark mode is active.
- **Security.**
  - A per-page `<meta http-equiv="Content-Security-Policy">` sets:
    - `default-src 'self'`
    - `img-src 'self' https://media.comphy-lab.org https://i.ytimg.com data:`
    - `media-src 'self' https://media.comphy-lab.org` (`'self'` lets the
      local test hook below play videos; the site origin itself serves no
      media)
    - `script-src 'self' https://www.youtube.com/iframe_api https://www.youtube.com/s/player/
      https://static.cloudflareinsights.com/beacon.min.js https://static.cloudflareinsights.com/beacon.min.js/`
      (the Cloudflare Web Analytics beacon the zone injects, as allowed on comphy-lab.org)
      (the IFrame API and the widget script it loads, nothing else on that host)
    - `frame-src https://www.youtube-nocookie.com`
    - `style-src 'self'`
    - `font-src 'self'`
    - `connect-src 'self' https://cloudflareinsights.com`
    - `base-uri 'none'`
    - `form-action 'none'`
    - `object-src 'none'`
  - No inline scripts or styles. DOM construction uses
    `createElement`/`textContent` only, never `innerHTML`.
- **Test hook.** When the page is served from `localhost` or `127.0.0.1`,
  `?media=<path>` may override `media_base` with a same-origin relative path.
  This override is ignored on any other host. The browser tests serve
  `site/` at `/talks/` and `build/<slug>/media/` at `/media/<slug>/`, as in
  production, and open `/talks/<slug>/?media=/media/<slug>/`.
  `window.__talk` is a frozen, read-only object for tests: `state()` returns
  `{slide, stateIndex, keys, paused}` (slide 1-based, stateIndex 0-based)
  and `media()` returns the mounted media as `[{key, el}]`.

### 3.3 Manifest invariants (checked by `validate`)

- `canvas` is 1920 x 1080 logical pixels for schemas 1 and 2.
- Every `ref` resolves to a `videos` or `youtube` entry.
- Every slide has at least one state, and slide `n` values are `1..N` in order.
- Every media file name matches `^[0-9a-f]{16}\.(webp|mp4)$`.
- `media_base` is `https://media.comphy-lab.org/talks/<slug>/`.
- No two placements in one state share a key.
- Density assets require schema 2, are supplied as an `image2x` / `overlay2x`
  pair, and agree with the original pair on whether an overlay exists.
- With `--media-dir`, density-bearing states are checked against their actual
  file dimensions: 1920 x 1080 for the original pair and 3840 x 2160 for 2x.
- A download's `file` is `<sha256-16>/<name>.<format>` with a matching
  extension, each format appears at most once, and no file exceeds 300 MB.
  With `--media-dir`, each file exists with the stated `bytes` and its
  directory is the hash of its content.

## 4. Video encoding

- **H.264** `libx264`, High profile, `yuv420p`, `-crf 23`, `-preset slow`,
  `-movflags +faststart`, no audio track. Width and height are even.
- **Scale.** At most `max_scale` times the largest on-slide rectangle the
  source occupies, and never upscaled.
- **Trim.** Keynote already pre-trims source files; the trim is encoded in the
  file name as `<name>-<start>-<end>.<ext>`. Encode the trimmed file as it is.
- **Poster.** The first frame as `webp`.
- **Deduplication.** The same source used on several slides is encoded once.
  Deduplicate by decoded stream identity (Keynote re-muxes copies with
  different container timestamps), not by file hash.

## 5. Site header and footer

The talks index and every talk page carry comphy-lab.org's header and footer,
so that `/talks/` reads as part of the main site. Markup and CSS are ported
from the main-site repository and vendored in `site/vendor/comphy-lab/`;
`site/vendor/comphy-lab/SOURCE.md` records the source commit, what was
taken, and every deliberate difference.

The shared shell uses the main site's explicit navigation colours, including
white labels and Close control in dark mode. A semantic check resolves one
main-site commit and compares ordered accessible navigation links, footer
columns and links, and the Menu breakpoint against both talks templates.
Pages validation fails on drift or a fetch/parse failure. A twice-monthly check on the 1st and 15th at 07:23 UTC
maintains one marked drift issue, skips unchanged reports, and does not create
an issue when the source cannot be fetched or parsed.

- **Header.** Fixed at the top, 80 px high, as on comphy-lab.org (60 px on a
  phone held sideways, at most 520 px high, to leave room for the slides).
  The page is padded by the header's height and in-page targets scroll clear
  of it; the talk page's stage and controls fit the viewport below it. From
  left to right: the lab logo linking to `/`, then the theme toggle, Google
  Scholar, GitHub, Team (`/team/`), Research (`/research`), Project docs
  (`/projects/`), Talks (`/talks/`), Teaching (`/teaching`), Join Us
  (`/join`), Blog and Bluesky. Talks is the current section: it carries
  `aria-current="page"` on the talks index, the page it links to, and
  `aria-current="true"` on a talk page, which lies inside that section. Links to the main site
  are root-relative, since the talks are served from the same origin. Icons
  are inline SVG; no icon font or CDN is loaded.
- **Menu drawer.** Below 1120 px wide (the same breakpoint as comphy-lab.org) the items move into a drawer that a
  "Menu" button opens (`aria-expanded`, `aria-controls`). Opening it moves
  focus into it and Tab stays inside it; Escape, the Close button, a click
  outside it or following one of its links closes it, and focus returns to
  the Menu button. A click outside only closes the drawer: it does not reach
  the stage. The drawer scrolls on short screens, is hidden once closed, and
  closes when the header becomes inert (full screen) or the window widens.
- **Keys.** The header handles no key the player uses. While the drawer is
  open it takes Escape (and cancels it) and Tab, and the player ignores every
  key, so the drawer behaves as a modal: the arrows and Page Up/Down scroll
  it, Space and Enter activate its links and buttons, and nothing changes the
  slide hidden behind it. The player still cancels Backspace, its "previous"
  key, since WebKit's default for it is History Back. Once the drawer is
  closed every key reaches the player, wherever the focus is.
- **Command palette.** comphy-lab.org's ⌘K palette is not included: it needs
  the main site's search index and Fuse.js, and a second global key handler
  on a talk page would compete with the player's shortcuts.
- **Footer.** comphy-lab.org's footer v2: brand blurb; Explore (Research,
  Project docs, News & talks, Talks, Team, Teaching); Work with us; Code &
  data; partner logos; legal strip. "Edit on GitHub" points at this
  repository. "Back to top" scrolls without changing the URL hash, which
  holds the talk's deep link.
- **Constraints.** The page CSP in Section 3.2 is unchanged: header and
  footer are static markup, styled from `site/vendor/comphy-lab/`, with
  behaviour in `site/player/header.js` (same origin, no inline script or
  style).
